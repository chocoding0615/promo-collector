"""출처별 하루 한도와 요청 간격. 한도를 넘지 않게 막습니다(대량 금지 출처 보호).

하루 집계는 state.json에 날짜별로 저장합니다(UTC 기준). GitHub Actions는 매번 새 VM이라
저장소에 commit되는 state.json을 읽어 이어서 셉니다.
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Budget:
    def __init__(self, state_path: str | Path):
        self.path = Path(state_path)
        self._data = {"day": _today(), "used": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if loaded.get("day") == _today():
                    self._data = loaded
            except Exception:
                pass  # 깨진 파일이면 오늘부터 새로 셈
        self._data.setdefault("used", {})
        self._last_call: dict[str, float] = {}

    def used(self, source: str) -> int:
        return int(self._data["used"].get(source, 0))

    def remaining(self, source: str, daily_limit: int) -> int:
        return max(0, daily_limit - self.used(source))

    def can_take(self, source: str, daily_limit: int, n: int = 1) -> bool:
        return self.remaining(source, daily_limit) >= n

    def take(self, source: str, n: int = 1) -> None:
        self._data["used"][source] = self.used(source) + n

    def wait_interval(self, source: str, min_interval_sec: float, sleep=time.sleep) -> None:
        """요청 간 최소 간격 유지(분당 한도 보호). sleep은 시험에서 가짜로 주입."""
        if min_interval_sec <= 0:
            return
        last = self._last_call.get(source)
        now = time.monotonic()
        if last is not None:
            gap = now - last
            if gap < min_interval_sec:
                sleep(min_interval_sec - gap)
        self._last_call[source] = time.monotonic()

    def save(self) -> None:
        self._data["day"] = _today()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
