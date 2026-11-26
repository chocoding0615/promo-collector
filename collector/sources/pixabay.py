"""Pixabay 검색·다운로드(소량 전용).

규칙(공식 문서 확인): 분당 100회 요청 한도, 같은 검색은 24시간 캐시, 대량 자동 다운로드 금지,
이미지 주소 영구 링크 금지(받아서 저장). → 하루 한도를 작게 두고 검색 결과는 24시간 재사용.
라이선스: Pixabay Content License(상업 이용 가능, 출처 표기 불필요). AI 이미지 거르는 옵션이 없어
CLIP 검사가 'AI풍'을 거릅니다.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any

from .wikimedia import Candidate

API = "https://pixabay.com/api/"
LICENSE = "Pixabay Content License"
CACHE_SEC = 24 * 3600


def build_params(key: str, query: str, per_query: int, offset: int) -> dict[str, Any]:
    n = max(3, min(int(per_query), 200))
    return {"key": key, "q": query[:100], "image_type": "photo", "safesearch": "true",
            "per_page": n, "page": offset // n + 1, "lang": "en"}


def parse(payload: dict[str, Any]) -> list[Candidate]:
    out: list[Candidate] = []
    for h in (payload or {}).get("hits") or []:
        url = h.get("largeImageURL") or h.get("webformatURL") or ""
        if not url:
            continue
        out.append(Candidate(
            source="pixabay", source_id=str(h.get("id", "")), title=f"pixabay_{h.get('id','')}.jpg",
            url=url, page_url=str(h.get("pageURL", "")), author=str(h.get("user", "")),
            license=LICENSE, width=int(h.get("imageWidth", 0) or 0), height=int(h.get("imageHeight", 0) or 0),
        ))
    return out


class _Cache:
    """검색 결과 24시간 캐시(state/pixabay_cache.json). Pixabay 규칙 준수용."""
    def __init__(self, path: Path):
        self.path = path
        try:
            self.d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            self.d = {}
        now = time.time()
        self.d = {k: v for k, v in self.d.items() if now - v.get("at", 0) < CACHE_SEC}

    def get(self, k: str):
        v = self.d.get(k)
        return v["payload"] if v else None

    def put(self, k: str, payload):
        self.d[k] = {"at": time.time(), "payload": payload}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")


_cache: _Cache | None = None


def use_cache(path: Path) -> None:
    global _cache
    _cache = _Cache(path)


def save_cache() -> None:
    if _cache:
        _cache.save()


def search(query: str, per_query: int, session, offset: int = 0, key: str = "") -> list[Candidate]:
    if not key:
        return []
    params = build_params(key, query, per_query, offset)
    ck = f"{params['q']}|{params['per_page']}|{params['page']}"
    payload = _cache.get(ck) if _cache else None
    if payload is None:
        r = session.get(API, params=params, timeout=30)
        if r.status_code == 400 and "page" in r.text.lower():
            return []  # 결과 끝(최대 500건)을 넘김
        r.raise_for_status()
        payload = r.json()
        if _cache:
            _cache.put(ck, payload)
    return parse(payload)


def download(cand: Candidate, session) -> bytes:
    r = session.get(cand.url, timeout=60)
    r.raise_for_status()
    return r.content
