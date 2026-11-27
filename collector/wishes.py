"""'수집 요청' — Promo가 사진 없음으로 적은 실제 장소를 먼저 받아 옴.

관리표 '수집 요청' 탭(A~H): 요청 시각, 카테고리, 장소, 영어 이름, 계정, 상태, 수집 장수, 메모
- 상태 '대기'/'진행'인 줄만 받음. 장소당 wish_need장(기본 6) 통과하면 '완료',
  모든 출처를 다 뒤져도 모자라면 '못 찾음'(받은 만큼은 관리표에 들어감). 그 밖엔 '진행'.
- 검색·자동 검사는 영어 이름으로: 위키데이터에서 한글 이름 → 영어 이름(키 없음, 무료).
  영어 이름을 못 찾으면 한글 그대로 검색하고 메모에 '영어명 없음'.
수집기는 이 탭의 D·F·G·H 칸만 고칩니다(줄 추가·삭제 없음).
"""
from __future__ import annotations

WISH_TAB = "수집 요청"
OPEN = ("대기", "진행")


def parse(values: list[list[str]]) -> list[dict]:
    """A2:H 값 → 받을 요청 목록(행 번호 포함). 순수 함수."""
    out = []
    for i, r in enumerate(values, start=2):
        r = [str(x).strip() for x in list(r) + [""] * (8 - len(r))]
        if not r[2] or not r[5].startswith(OPEN):
            continue
        out.append({"row": i, "category": r[1] or "여행", "place": r[2], "en": r[3], "status": r[5]})
    return out


def status_for(kept: int, need: int, exhausted: bool) -> str:
    if kept >= need:
        return "완료"
    return "못 찾음" if exhausted else "진행"


def english_name(place: str, session) -> tuple[str, str]:
    """한글 장소명 → (영어 이름, 메모). 전체 이름 → 마지막 낱말 순서로 위키데이터 검색."""
    tries = [place]
    parts = place.split()
    if len(parts) > 1:
        tries.append(parts[-1])
    for q in tries:
        try:
            r = session.get("https://www.wikidata.org/w/api.php", params={
                "action": "wbsearchentities", "search": q, "language": "ko", "uselang": "en",
                "type": "item", "limit": 1, "format": "json"}, timeout=20)
            hits = r.json().get("search", [])
            if not hits:
                continue
            qid = hits[0]["id"]
            e = session.get("https://www.wikidata.org/w/api.php", params={
                "action": "wbgetentities", "ids": qid, "props": "labels", "languages": "en", "format": "json"},
                timeout=20).json()
            en = e.get("entities", {}).get(qid, {}).get("labels", {}).get("en", {}).get("value", "")
            if en:
                return en, f"위키데이터 {qid}"
        except Exception:
            continue
    return place, "영어명 없음"
