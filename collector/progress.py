"""주제별 진행 상태(state/progress.json). 실행마다 새 컴퓨터라 여기 적어 두고 이어서 받습니다.

- kept: 이 주제로 통과한 장수(목표 need를 채우면 건너뜀)
- offset[출처]: 다음에 받을 검색 결과 위치(같은 결과를 다시 받지 않게)
- done[출처]: 이 출처에 더 받을 결과가 없음
- rr: 다음 실행이 시작할 주제 위치(매시간 다른 주제부터 고르게)
"""
from __future__ import annotations
import json
from pathlib import Path


class Progress:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.d = {"subjects": {}, "rr": 0}
        if self.path.exists():
            try:
                self.d = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.d.setdefault("subjects", {})
        self.d.setdefault("rr", 0)

    def _s(self, key: str) -> dict:
        return self.d["subjects"].setdefault(key, {"kept": 0, "offset": {}, "done": {}})

    def kept(self, key: str) -> int:
        return int(self._s(key)["kept"])

    def add_kept(self, key: str, n: int = 1) -> None:
        self._s(key)["kept"] = self.kept(key) + n

    def offset(self, key: str, src: str) -> int:
        return int(self._s(key)["offset"].get(src, 0))

    def advance(self, key: str, src: str, n: int) -> None:
        s = self._s(key)
        s["offset"][src] = self.offset(key, src) + n

    def is_done(self, key: str, src: str) -> bool:
        return bool(self._s(key)["done"].get(src))

    def mark_done(self, key: str, src: str) -> None:
        self._s(key)["done"][src] = True

    @property
    def rr(self) -> int:
        return int(self.d.get("rr", 0))

    @rr.setter
    def rr(self, v: int) -> None:
        self.d["rr"] = int(v)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")


def rotate(items: list, start: int) -> list:
    """start 위치부터 한 바퀴 돌린 목록(매 실행 다른 주제부터)."""
    if not items:
        return []
    k = start % len(items)
    return items[k:] + items[:k]
