"""Openverse 검색·다운로드(소량 전용).

규칙: 키 없이 쓰면 검색당 최대 240건(20개×12쪽), 스크래핑 금지 → 하루 한도를 작게.
상업 이용 + 수정(카드에 글자를 얹음) 가능한 라이선스만, 사진 분류만 받습니다(ND·NC 제외).
"""
from __future__ import annotations
from typing import Any

from .wikimedia import Candidate

API = "https://api.openverse.org/v1/images/"
MAX_PAGE = 12  # 키 없는 사용자 상한(240건)


def build_params(query: str, per_query: int, offset: int) -> dict[str, Any] | None:
    n = max(1, min(int(per_query), 20))
    page = offset // n + 1
    if page > MAX_PAGE:
        return None
    return {"q": query[:200], "page": page, "page_size": n, "category": "photograph",
            "license_type": "commercial,modification", "mature": "false"}


def license_label(lic: str, ver: str) -> str:
    lic = (lic or "").lower()
    if lic == "cc0":
        return "CC0"
    if lic == "pdm":
        return "PDM"
    return f"CC {lic.upper()} {ver}".strip()


def parse(payload: dict[str, Any]) -> list[Candidate]:
    out: list[Candidate] = []
    for h in (payload or {}).get("results") or []:
        lic = str(h.get("license", "")).lower()
        if "nd" in lic or "nc" in lic:
            continue  # 혹시 섞여도 제외
        url = h.get("url") or ""
        if not url:
            continue
        out.append(Candidate(
            source="openverse", source_id=str(h.get("id", "")), title=str(h.get("title") or f"openverse_{h.get('id','')}")[:120],
            url=url, page_url=str(h.get("foreign_landing_url") or ""), author=str(h.get("creator") or ""),
            license=license_label(lic, str(h.get("license_version") or "")),
            width=int(h.get("width") or 0), height=int(h.get("height") or 0),
        ))
    return out


def search(query: str, per_query: int, session, offset: int = 0, key: str = "") -> list[Candidate]:
    params = build_params(query, per_query, offset)
    if params is None:
        return []
    r = session.get(API, params=params, timeout=30)
    r.raise_for_status()
    return parse(r.json())


def download(cand: Candidate, session) -> bytes:
    r = session.get(cand.url, timeout=60)
    r.raise_for_status()
    return r.content
