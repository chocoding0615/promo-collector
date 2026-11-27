"""수집기 실행기 — 출처별 시간 분배.

매시간 실행. 출처마다 이번 시간 몫(하루 한도 ÷ 하루 실행 횟수)만 받습니다.
주제는 매번 다른 위치부터 돌고(rr), 주제마다 검색 결과 위치(offset)를 기억해 다음 페이지를 이어 받습니다.

흐름: 주제 → 카테고리의 출처 순서대로(이번 시간 몫이 남은 출처) 검색 → 받기 → 2000px로 정리
     → 중복·크기·사진·얼굴·글자·주제 검사 → 통과분 Drive 업로드 → 끝나고 관리표에 한 번에 기록

모드: --dry-run(받지 않고 이번 실행 계획만) · --max-keep N(시험: 통과 N장에서 멈춤)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, ratelimit
from .checks.dedup import Deduper
from .pipeline import decide
from . import policy as policymod
from . import wishes as wishmod
from .progress import Progress, rotate
from .sources import SOURCES

ROOT = Path(__file__).resolve().parents[1]
REJECT_PER_DAY = 20     # 탈락 샘플 하루 최대
REJECT_PER_RUN = 2      # 한 번 실행에 최대


def pick_samples(cands: dict[str, dict], by_reason_today: dict[str, int], n: int) -> list[dict]:
    """이유별 후보 1장씩 중 오늘 적게 저장된 이유부터 n장(이유가 골고루 섞이게)."""
    order = sorted(cands, key=lambda r: (by_reason_today.get(r, 0), r))
    return [cands[r] for r in order[:max(0, n)]]


def subjects(cfg: config.Config) -> list[dict]:
    """모든 주제를 카테고리를 섞어 고르게 나열(카테고리 하나에 몰리지 않게)."""
    per_cat = []
    for cat in cfg.categories.values():
        per_cat.append([{"category": cat.name, "target": t, "need": cat.need, "order": cat.source_order}
                        for t in cat.targets])
    out, i = [], 0
    while any(per_cat):
        for lst in per_cat:
            if i < len(lst):
                out.append(lst[i])
        i += 1
        if all(i >= len(l) for l in per_cat):
            break
    return out


def available_sources(cfg: config.Config, env=os.environ) -> list[str]:
    return [n for n, s in cfg.sources.items()
            if s.enabled and n in SOURCES and (not s.needs_key or env.get(s.needs_key))]


def plan(cfg: config.Config, budget_used: dict[str, int] | None = None, env=os.environ) -> dict:
    """이번 실행 계획(네트워크 없음): 출처별 이번 시간 몫, 쓸 수 있는 출처, 주제 수."""
    used = budget_used or {}
    srcs = available_sources(cfg, env)
    return {
        "caps": {s: cfg.run_cap(s, used.get(s, 0)) for s in srcs},
        "skipped_sources": [n for n in cfg.sources if n not in srcs],
        "subjects": len(subjects(cfg)),
    }


def run(cfg: config.Config, *, dry_run: bool, out_dir: Path, state_dir: Path, max_keep: int = 0) -> dict:
    budget = ratelimit.Budget(state_dir / "state.json")
    used_today = {s: budget.used(s) for s in cfg.sources}
    p = plan(cfg, used_today)
    if dry_run:
        print(json.dumps({"dry_run": True, **p}, ensure_ascii=False))
        for it in subjects(cfg)[:10]:
            print(f"[dry-run] {it['category']} · {it['target']} → {it['order']} (목표 {it['need']})")
        return {"dry_run": True, **p}

    from .http import make_session
    from .imageutil import normalize
    from .config import split_target
    from .sources import pixabay
    session = make_session()
    progress = Progress(state_dir / "progress.json")
    pixabay.use_cache(state_dir / "pixabay_cache.json")
    deduper = Deduper(cfg.checks.dedup_max_distance)
    from .checks import vision

    caps = dict(p["caps"])                       # 이번 실행 출처별 남은 몫
    took = {s: 0 for s in caps}
    deadline = time.monotonic() + cfg.max_run_minutes * 60
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"kept": 0, "rejected": 0, "uploaded": 0, "reasons": {}, "by_source": {},
              "kept_by_source": {}, "caps": dict(caps), "skipped_sources": p["skipped_sources"]}
    err = lambda m: report.setdefault("errors", []).append(m[:200])

    from . import drive as drivemod
    uploader, pending_rows = None, []
    if drivemod.creds_available():
        folder, sheet = os.environ.get("DRIVE_FOLDER_ID", ""), os.environ.get("SHEET_ID", "")
        if folder and sheet:
            uploader = drivemod.Uploader(folder, sheet)
            try:
                import imagehash
                for h in uploader.known_phashes():
                    deduper.add(imagehash.hex_to_hash(h))
            except Exception as e:
                err(f"known_phashes: {type(e).__name__}")
        else:
            err("DRIVE_FOLDER_ID/SHEET_ID 비어 있음 → 업로드 생략")

    pol = policymod.Policy(state_dir / "policy.json")
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rejects = None
    if uploader is not None:
        rejects = drivemod.RejectStore(uploader)
        try:
            js = rejects.judgments()
            report["policy_changes"] = policymod.evaluate(js, pol, now_iso)
            report["error_rates"] = policymod.error_rates(js, pol)
        except Exception as e:
            err(f"judgments: {type(e).__name__}: {str(e)[:120]}")
    levels = pol.levels()
    report["policy_levels"] = levels
    sample_cands: dict[str, dict] = {}   # 이유 → 이번 실행 후보 1장

    def left(src):  # 이번 실행에서 이 출처가 더 받을 수 있나
        return took.get(src, 0) < caps.get(src, 0)

    # 수집 요청(Promo 사진 없음 장소)을 맨 앞에
    wish_items = []
    if uploader is not None:
        try:
            for w in wishmod.parse(uploader.wish_values()):
                en, memo = (w["en"], "") if w["en"] else wishmod.english_name(w["place"], session)
                cat = cfg.categories.get(w["category"])
                order = cat.source_order if cat else ["wikimedia", "openverse"]
                wish_items.append({"category": w["category"], "target": f"{w['place']}|{en}", "need": cfg.wish_need,
                                   "order": order, "wish": {**w, "en": en, "memo": memo}})
        except Exception as e:
            err(f"wishes: {type(e).__name__}: {str(e)[:120]}")
    report["wishes"] = len(wish_items)

    items = wish_items + rotate(subjects(cfg), progress.rr)
    visited = 0
    for it in items:
        if time.monotonic() > deadline or not any(left(s) for s in caps):
            break
        if max_keep and report["kept"] >= max_keep:
            break
        visited += 0 if it.get("wish") else 1
        ko, en = split_target(it["target"])
        key = f"wish|{ko}" if it.get("wish") else f"{it['category']}|{ko}"
        if progress.kept(key) >= it["need"]:
            continue
        for src in it["order"]:
            if src not in caps or not left(src) or progress.is_done(key, src):
                continue
            mod, scfg = SOURCES[src], cfg.sources[src]
            budget.wait_interval(src, scfg.min_interval_sec)
            off = progress.offset(key, src)
            try:
                cands = mod.search(en, scfg.per_query, session, offset=off,
                                   key=os.environ.get(scfg.needs_key, "") if scfg.needs_key else "")
            except Exception as e:
                err(f"search {src} {ko}: {type(e).__name__}")
                continue
            if not cands:
                progress.mark_done(key, src)
                continue
            progress.advance(key, src, len(cands))
            for c in cands:
                if not left(src) or progress.kept(key) >= it["need"] or time.monotonic() > deadline:
                    break
                if max_keep and report["kept"] >= max_keep:
                    break
                budget.wait_interval(src, scfg.min_interval_sec)
                try:
                    raw = mod.download(c, session)
                    data, w, h = normalize(raw)
                except Exception as e:
                    err(f"download {src}: {type(e).__name__}")
                    continue
                took[src] += 1
                budget.take(src, 1)
                report["by_source"][src] = report["by_source"].get(src, 0) + 1
                is_dup, hexh = deduper.check_bytes(data)
                v = vision.inspect(data, subject=en)
                flags, checks_now = policymod.judge(v, levels, cfg.checks)
                verdict = decide(checks_now, long_side=max(w, h), is_duplicate=is_dup, **flags)
                if not verdict.ok:
                    report["rejected"] += 1
                    keys = [policymod.reason_key(r) for r in verdict.reasons]
                    for r in keys:
                        report["reasons"][r] = report["reasons"].get(r, 0) + 1
                    # 샘플 후보: 이유가 하나뿐인 탈락만(오판 판정이 어느 기준 탓인지 분명하게), 중복 제외
                    if len(keys) == 1 and keys[0] in policymod.RELAX and keys[0] not in sample_cands:
                        sample_cands[keys[0]] = {
                            "data": data, "reason": verdict.reasons[0], "key": keys[0],
                            "meta": {"category": it["category"], "subject": ko, "source": c.source,
                                     "page_url": c.page_url},
                            "scores": f"사진{v.photo_margin:+.3f} 얼굴{v.face_margin:+.3f} "
                                      f"글자{v.text_margin:+.3f} 주제{v.subject_score:.2f} {w}x{h}",
                            "stem": f"reject_{src}_{hexh[:12]}",
                        }
                    continue
                stem = f"{src}_{hexh[:12]}"
                meta = {"category": it["category"], "subject": ko, "source": c.source,
                        "source_id": c.source_id, "title": c.title, "page_url": c.page_url,
                        "author": c.author, "license": c.license, "phash": hexh, "width": w, "height": h}
                (out_dir / f"{stem}.jpg").write_bytes(data)
                report["kept"] += 1
                report["kept_by_source"][src] = report["kept_by_source"].get(src, 0) + 1
                progress.add_kept(key)
                if uploader is not None:
                    try:
                        fid = uploader.upload(data, f"{stem}.jpg")
                        pending_rows.append(drivemod.build_row(meta, fid))
                        report["uploaded"] += 1
                    except Exception as e:
                        err(f"upload {stem}: {type(e).__name__}: {str(e)[:120]}")
            if progress.kept(key) >= it["need"]:
                break

    progress.rr = progress.rr + max(1, visited)   # 다음 실행은 이어서 다른 주제부터
    # 수집 요청 상태 갱신(D 영어 이름·F 상태·G 장수·H 메모)
    if uploader is not None and wish_items:
        ups = []
        for it in wish_items:
            w = it["wish"]; key = f"wish|{w['place']}"
            kept = progress.kept(key)
            exhausted = all(s not in caps or progress.is_done(key, s) for s in it["order"])
            st = wishmod.status_for(kept, it["need"], exhausted)
            ups.append({"row": w["row"], "en": w["en"], "status": st, "count": kept,
                        "memo": " · ".join(x for x in [w.get("memo", ""), f"수집기 {now_iso[:16]}"] if x)})
        report["wish_status"] = {u["en"] or str(u["row"]): f"{u['status']}({u['count']})" for u in ups}
        try:
            uploader.update_wishes(ups)
        except Exception as e:
            err(f"update_wishes: {type(e).__name__}: {str(e)[:120]}")
    if uploader is not None and pending_rows:
        try:
            uploader.append_rows(pending_rows)
        except Exception as e:
            err(f"append_rows: {type(e).__name__}: {str(e)[:120]}")
    # 탈락 샘플 저장(하루 20장, 실행당 2장, 이유 골고루)
    day = now_iso[:10]
    today = pol.samples_today(day)
    n = min(REJECT_PER_RUN, REJECT_PER_DAY - today["n"])
    saved = []
    if rejects is not None and n > 0 and sample_cands:
        rows = []
        for sc in pick_samples(sample_cands, today["by_reason"], n):
            try:
                fid = rejects.upload(sc["data"], f"{sc['stem']}.jpg")
                rows.append(drivemod.build_reject_row(sc["meta"], fid, sc["reason"], sc["scores"], now_iso))
                pol.count_sample(day, sc["key"])
                saved.append(sc["key"])
            except Exception as e:
                err(f"reject upload: {type(e).__name__}: {str(e)[:120]}")
        try:
            rejects.write_rows(rows)
        except Exception as e:
            err(f"reject rows: {type(e).__name__}: {str(e)[:120]}")
    report["reject_samples"] = saved
    pol.save()
    budget.save()
    progress.save()
    pixabay.save_cache()
    report["took"] = took
    report["at"] = datetime.now(timezone.utc).isoformat()
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "last_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="promo-collector")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--state", default=str(ROOT / "state"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-keep", type=int, default=0, help="시험용: 통과 N장에서 멈춤(0=제한 없음)")
    args = ap.parse_args(argv)
    cfg = config.load(args.config)
    run(cfg, dry_run=args.dry_run, out_dir=Path(args.out), state_dir=Path(args.state), max_keep=args.max_keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
