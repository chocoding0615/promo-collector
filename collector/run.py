"""수집기 실행기.

흐름: 카테고리별 목표 주제 → 출처 순서대로 검색(한도 안에서) → 받기 → 중복·사진·얼굴·글자·주제 검사
     → 통과분을 out/ 에 저장(+메타). Drive 업로드·관리표 기록은 다음 단계에서 붙입니다.

모드
  --dry-run   : 받지 않고 "어떤 주제를 어느 출처에서 몇 장 찾을지"만 출력(네트워크 없음)
  (기본)      : 실제 검색·다운로드·검사. 모델·네트워크가 필요하므로 Actions에서 검증

끝나면 state.json(하루 한도 집계)과 report를 남깁니다.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config, ratelimit
from .checks.dedup import Deduper
from .pipeline import decide
from .sources import wikimedia

ROOT = Path(__file__).resolve().parents[1]


def plan(cfg: config.Config) -> list[dict]:
    """각 카테고리/주제를 어느 출처에서 몇 장 찾을지 계획만 세움(네트워크 없음)."""
    items = []
    for cat in cfg.categories.values():
        for subject in cat.targets:
            src = next((s for s in cat.source_order if cfg.sources[s].enabled), None)
            items.append({"category": cat.name, "subject": subject,
                          "source": src, "need": cat.need})
    return items


def _load_known_hashes(path: Path) -> list[str]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def run(cfg: config.Config, *, dry_run: bool, out_dir: Path, state_dir: Path) -> dict:
    items = plan(cfg)
    if dry_run:
        for it in items:
            print(f"[dry-run] {it['category']} · {it['subject']} → {it['source']} (목표 {it['need']})")
        return {"dry_run": True, "planned": len(items)}

    from .http import make_session  # 실제 실행에서만 필요
    session = make_session()
    budget = ratelimit.Budget(state_dir / "state.json")
    deduper = Deduper(cfg.checks.dedup_max_distance,
                      known_hex=_load_known_hashes(state_dir / "known_hashes.json"))
    from .checks import vision  # 모델 로드(무거움) — 실제 실행에서만

    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"kept": 0, "rejected": 0, "reasons": {}, "by_source": {}}

    for it in items:
        src_name = it["source"]
        if not src_name:
            continue
        scfg = cfg.sources[src_name]
        if not budget.can_take(src_name, scfg.daily_limit, 1):
            continue  # 오늘 이 출처 한도 끝
        budget.wait_interval(src_name, scfg.min_interval_sec)
        if src_name == "wikimedia":
            try:
                cands = wikimedia.search(it["subject"], min(scfg.per_query, it["need"] * 4), session)
            except Exception as e:  # 한 주제 실패가 전체를 멈추지 않게
                report.setdefault("errors", []).append(f"{it['subject']}: {type(e).__name__}")
                continue
        else:
            # pixabay/openverse 어댑터는 다음 커밋에서. 지금은 건너뜀.
            continue
        kept_for_item = 0
        for c in cands:
            if kept_for_item >= it["need"]:
                break
            if not budget.can_take(src_name, scfg.daily_limit, 1):
                break
            budget.wait_interval(src_name, scfg.min_interval_sec)
            try:
                data = wikimedia.download(c, session)
            except Exception:
                continue
            budget.take(src_name, 1)
            report["by_source"][src_name] = report["by_source"].get(src_name, 0) + 1
            is_dup, hexh = deduper.check_bytes(data)
            v = vision.inspect(data, subject=it["subject"])
            verdict = decide(cfg.checks, long_side=max(c.width, c.height),
                             is_photo=v.is_photo, subject_score=v.subject_score,
                             face=v.face, has_text=v.has_text, is_duplicate=is_dup)
            if not verdict.ok:
                report["rejected"] += 1
                for r in verdict.reasons:
                    report["reasons"][r] = report["reasons"].get(r, 0) + 1
                continue
            stem = f"{it['category']}_{hexh[:12]}"
            (out_dir / f"{stem}.jpg").write_bytes(data)
            (out_dir / f"{stem}.json").write_text(json.dumps({
                "category": it["category"], "subject": it["subject"], "source": c.source,
                "source_id": c.source_id, "title": c.title, "page_url": c.page_url,
                "author": c.author, "license": c.license, "phash": hexh,
                "width": c.width, "height": c.height,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            report["kept"] += 1
            kept_for_item += 1

    budget.save()
    report["at"] = datetime.now(timezone.utc).isoformat()
    (state_dir / "last_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="promo-collector")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--state", default=str(ROOT / "state"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = config.load(args.config)
    run(cfg, dry_run=args.dry_run, out_dir=Path(args.out), state_dir=Path(args.state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
