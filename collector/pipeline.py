"""검사 결과 → 통과/탈락 판정. 판정 규칙은 순수 함수라 로컬에서 시험 가능.

실제 사진을 받아 검사하는 run()은 모델이 필요해 Actions에서 검증합니다.
"""
from __future__ import annotations
import dataclasses

from .config import ChecksCfg


@dataclasses.dataclass
class Verdict:
    ok: bool
    reasons: list[str]


def decide(checks: ChecksCfg, *, long_side: int, is_photo: bool, subject_score: float,
           face: bool, has_text: bool, is_duplicate: bool) -> Verdict:
    """한 장의 검사 결과를 설정 기준과 대조해 통과 여부를 정합니다."""
    r: list[str] = []
    if is_duplicate:
        r.append("중복")
    if long_side < checks.min_long_side:
        r.append(f"너무 작음({long_side}px)")
    if checks.require_photo and not is_photo:
        r.append("그림·삽화·AI풍(사진 아님)")
    if not checks.allow_faces and face:
        r.append("얼굴 있음")
    if has_text:
        r.append("글자·로고·워터마크")
    if subject_score < checks.min_subject_score:
        r.append(f"주제와 약함({subject_score:.2f})")
    return Verdict(ok=not r, reasons=r)
