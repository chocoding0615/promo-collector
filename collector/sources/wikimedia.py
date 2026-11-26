"""Wikimedia Commons 검색·다운로드.

- 검색어 생성과 API URL 구성은 네트워크 없이 시험 가능(build_search_params).
- 실제 fetch는 GitHub Actions(열린 인터넷)에서 검증합니다.
- 자유 이용 사진 위주이고 대량 수집을 허용하지만, 예의상 User-Agent를 명확히 보냅니다.
"""
from __future__ import annotations
import dataclasses
from typing import Any

API = "https://commons.wikimedia.org/w/api.php"
UA = "promo-collector/0.1 (contact: set in repo; non-commercial image sourcing)"


@dataclasses.dataclass
class Candidate:
    source: str
    source_id: str      # 파일 제목(File:...)
    title: str          # 사람이 읽는 이름
    url: str            # 원본 이미지 URL
    page_url: str       # 설명 페이지
    author: str         # 작가(없으면 "")
    license: str        # 라이선스 문자열
    width: int
    height: int


def build_search_params(query: str, per_query: int) -> dict[str, Any]:
    """파일 네임스페이스(6)에서 비트맵 이미지만 검색. 검색어는 그대로 전달."""
    n = max(1, min(int(per_query), 50))
    return {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": "6",
        "gsrlimit": n,
        "prop": "imageinfo",
        "iiprop": "url|size|extmetadata|mime",
        "iiurlwidth": "1600",
    }


def _ext(meta: dict[str, Any], key: str) -> str:
    v = (meta or {}).get(key)
    if isinstance(v, dict):
        return str(v.get("value", "")).strip()
    return ""


def parse_search(payload: dict[str, Any]) -> list[Candidate]:
    """API 응답(JSON) → 후보 목록. 네트워크 없이 시험 가능(고정 payload 사용)."""
    out: list[Candidate] = []
    pages = ((payload or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime", ""))
        if not mime.startswith("image/"):
            continue
        meta = info.get("extmetadata") or {}
        out.append(Candidate(
            source="wikimedia",
            source_id=str(page.get("title", "")),
            title=str(page.get("title", "")).removeprefix("File:"),
            url=str(info.get("url", "")),
            page_url=str(info.get("descriptionurl", "")),
            author=_ext(meta, "Artist") or _ext(meta, "Credit"),
            license=_ext(meta, "LicenseShortName") or _ext(meta, "License"),
            width=int(info.get("width", 0) or 0),
            height=int(info.get("height", 0) or 0),
        ))
    return out


def search(query: str, per_query: int, session) -> list[Candidate]:
    """실제 검색(네트워크). session은 requests.Session 유사 객체."""
    r = session.get(API, params=build_search_params(query, per_query),
                     headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return parse_search(r.json())


def download(cand: Candidate, session) -> bytes:
    r = session.get(cand.url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.content
