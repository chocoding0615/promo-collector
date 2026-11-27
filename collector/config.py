"""설정 읽기와 검증. 네트워크·모델이 필요 없어 로컬에서 시험 가능."""
from __future__ import annotations
import dataclasses
import math
from pathlib import Path
from typing import Any
import yaml


@dataclasses.dataclass
class SourceCfg:
    name: str
    enabled: bool
    daily_limit: int
    per_query: int
    min_interval_sec: float = 0.0
    needs_key: str = ""          # 비밀값 이름(예: PIXABAY_KEY). 없으면 이 출처는 건너뜀


@dataclasses.dataclass
class CategoryCfg:
    name: str
    targets: list[str]           # "한글|영어" — 한글은 관리표 주제, 영어는 검색어
    source_order: list[str]
    need: int


@dataclasses.dataclass
class ChecksCfg:
    dedup_max_distance: int = 5
    allow_faces: bool = False
    require_photo: bool = True
    min_subject_score: float = 0.22
    min_long_side: int = 1000


@dataclasses.dataclass
class Config:
    sources: dict[str, SourceCfg]
    categories: dict[str, CategoryCfg]
    checks: ChecksCfg
    runs_per_day: int = 24
    max_run_minutes: int = 40
    wish_need: int = 6

    def enabled_sources(self) -> list[str]:
        return [n for n, s in self.sources.items() if s.enabled]

    def run_cap(self, source: str, used_today: int) -> int:
        """이번 실행에서 이 출처가 받을 수 있는 수 = 하루 한도를 하루 실행 횟수로 나눈 몫(남은 한도 이내)."""
        s = self.sources[source]
        per_run = math.ceil(s.daily_limit / max(1, self.runs_per_day))
        return max(0, min(per_run, s.daily_limit - used_today))


def split_target(t: str) -> tuple[str, str]:
    """'경복궁|Gyeongbokgung' → ('경복궁', 'Gyeongbokgung'). 영어가 없으면 한글로 검색."""
    ko, _, en = str(t).partition("|")
    ko, en = ko.strip(), en.strip()
    return ko, (en or ko)


def load(path: str | Path) -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = {}
    for name, s in (raw.get("sources") or {}).items():
        sources[name] = SourceCfg(
            name=name,
            enabled=bool(s.get("enabled", False)),
            daily_limit=int(s.get("daily_limit", 0)),
            per_query=int(s.get("per_query", 20)),
            min_interval_sec=float(s.get("min_interval_sec", 0)),
            needs_key=str(s.get("needs_key", "") or ""),
        )
    if not sources:
        raise ValueError("sources가 비어 있습니다")
    categories = {}
    for name, c in (raw.get("categories") or {}).items():
        order = [o for o in (c.get("source_order") or []) if o in sources]
        if not order:
            raise ValueError(f"카테고리 '{name}'의 source_order가 비어 있거나 없는 출처를 가리킵니다")
        categories[name] = CategoryCfg(
            name=name,
            targets=[str(t) for t in (c.get("targets") or [])],
            source_order=order,
            need=int(c.get("need", 5)),
        )
    checks = ChecksCfg(**(raw.get("checks") or {}))
    return Config(sources=sources, categories=categories, checks=checks,
                  runs_per_day=int(raw.get("runs_per_day", 24)),
                  max_run_minutes=int(raw.get("max_run_minutes", 40)),
                  wish_need=int(raw.get("wish_need", 6)))
