"""탈락 기준 자동 완화.

안티그래피티가 '탈락 샘플' 탭의 판정 칸에 '오판'(통과했어야 함) 또는 '정상'을 적습니다.
수집기는 매 실행 시작에 이유별로 판정을 세어,
  · 판정 10장 이상 · 오판 비율 50% 초과 → 그 이유의 기준만 한 단계 완화
  · 하루 한 단계까지, 최대 3단계
  · 완화한 뒤에는 그 이후 기록된 샘플의 판정만 다시 셈(이전 기준의 판정이 섞이지 않게)
기준을 조이는(되돌리는) 일은 자동으로 하지 않습니다 — 사람이 state/policy.json의 level을 내리면 됩니다.
"""
from __future__ import annotations
import dataclasses
import json
from pathlib import Path

from .config import ChecksCfg

MIN_JUDGED = 10
MAX_ERROR = 0.5
MAX_LEVEL = 3

# 이유별 단계 값(0단계 = 원래 기준)
#  그림·얼굴·글자: CLIP 경계를 몇 점(코사인) 옮길지. 0.01이 대략 '애매한 사진 일부 통과' 정도
#  너무 작음: 긴 변 최소 px / 주제와 약함: 최소 일치 점수
RELAX: dict[str, list[float]] = {
    "그림·삽화·AI풍": [0.0, 0.01, 0.02, 0.03],
    "얼굴 있음": [0.0, 0.01, 0.02, 0.03],
    "글자·로고·워터마크": [0.0, 0.01, 0.02, 0.03],
    "너무 작음": [1000, 900, 850, 800],
    "주제와 약함": [0.22, 0.18, 0.15, 0.12],
}


def reason_key(reason: str) -> str:
    """'주제와 약함(0.12)' → '주제와 약함'"""
    return reason.split("(")[0].strip()


class Policy:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        self.data.setdefault("rules", {})
        self.data.setdefault("samples", {})

    def level(self, reason: str) -> int:
        return int(self.data["rules"].get(reason, {}).get("level", 0))

    def since(self, reason: str) -> str:
        return self.data["rules"].get(reason, {}).get("since", "")

    def levels(self) -> dict[str, int]:
        return {r: self.level(r) for r in RELAX}

    # 탈락 샘플 하루 개수(UTC 날짜 기준)
    def samples_today(self, day: str) -> dict:
        s = self.data["samples"]
        if s.get("day") != day:
            s.clear()
            s.update({"day": day, "n": 0, "by_reason": {}})
        return s

    def count_sample(self, day: str, reason: str) -> None:
        s = self.samples_today(day)
        s["n"] += 1
        s["by_reason"][reason] = s["by_reason"].get(reason, 0) + 1

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")


def error_rates(rows: list[dict], policy: Policy) -> dict[str, dict]:
    """rows: {'at': 기록 시각 ISO, 'reason': 이유, 'verdict': 판정} → 이유별 {judged, wrong, rate}.
    각 이유의 마지막 완화 시각 이후 기록된 샘플만 셈."""
    out: dict[str, dict] = {}
    for r in rows:
        reason = reason_key(r.get("reason", ""))
        v = str(r.get("verdict", "")).strip()
        if reason not in RELAX or v not in ("오판", "정상"):
            continue
        if str(r.get("at", "")) < policy.since(reason):
            continue
        o = out.setdefault(reason, {"judged": 0, "wrong": 0})
        o["judged"] += 1
        o["wrong"] += v == "오판"
    for o in out.values():
        o["rate"] = round(o["wrong"] / o["judged"], 3) if o["judged"] else 0.0
    return out


def evaluate(rows: list[dict], policy: Policy, now_iso: str) -> list[dict]:
    """오류율을 보고 필요한 이유만 한 단계 완화. 바뀐 내용 목록을 돌려줌(정책 객체도 갱신)."""
    today = now_iso[:10]
    changes = []
    for reason, o in error_rates(rows, policy).items():
        rule = policy.data["rules"].setdefault(reason, {"level": 0, "since": "", "changed_at": ""})
        if o["judged"] < MIN_JUDGED or o["rate"] <= MAX_ERROR:
            continue
        if rule.get("level", 0) >= MAX_LEVEL or str(rule.get("changed_at", ""))[:10] == today:
            continue
        before = rule.get("level", 0)
        rule.update({"level": before + 1, "since": now_iso, "changed_at": now_iso})
        changes.append({"reason": reason, "from": before, "to": before + 1,
                        "judged": o["judged"], "wrong": o["wrong"], "rate": o["rate"],
                        "value": RELAX[reason][before + 1]})
    return changes


def judge(v, levels: dict[str, int], base: ChecksCfg) -> tuple[dict, ChecksCfg]:
    """CLIP 결과(v)와 현재 단계 → decide()에 넘길 판정값과 조정된 기준."""
    lv = lambda r: min(levels.get(r, 0), MAX_LEVEL)
    a_photo = RELAX["그림·삽화·AI풍"][lv("그림·삽화·AI풍")]
    a_face = RELAX["얼굴 있음"][lv("얼굴 있음")]
    a_text = RELAX["글자·로고·워터마크"][lv("글자·로고·워터마크")]
    checks = dataclasses.replace(
        base,
        min_long_side=min(base.min_long_side, int(RELAX["너무 작음"][lv("너무 작음")])),
        min_subject_score=min(base.min_subject_score, float(RELAX["주제와 약함"][lv("주제와 약함")])),
    )
    flags = {
        "is_photo": v.photo_margin >= -a_photo,
        "face": v.face_margin > a_face,
        "has_text": v.text_margin > a_text,
        "subject_score": v.subject_score,
    }
    return flags, checks
