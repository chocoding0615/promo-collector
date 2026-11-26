"""설정 읽기와 검증. 네트워크·모델이 필요 없어 로컬에서 시험 가능."""
from __future__ import annotations
import dataclasses
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


@dataclasses.dataclass
class CategoryCfg:
    name: str
    targets: list[str]
    source_order: list[str]
    need: int


@dataclasses.dataclass
class ChecksCfg:
    dedup_max_distance: int = 5
    allow_faces: bool = False
    max_text_ratio: float = 0.08
    require_photo: bool = True
    min_subject_score: float = 0.22
    min_long_side: int = 1000


@dataclasses.dataclass
class Config:
    sources: dict[str, SourceCfg]
    categories: dict[str, CategoryCfg]
    checks: ChecksCfg

    def enabled_sources(self) -> list[str]:
        return [n for n, s in self.sources.items() if s.enabled]


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
        )
    categories = {}
    for name, c in (raw.get("categories") or {}).items():
        order = [o for o in (c.get("source_order") or []) if o in sources]
        if not order:
            raise ValueError(f"카테고리 '{name}'의 source_order가 비어 있거나 없는 출처를 가리킵니다")
        categories[name] = CategoryCfg(
            name=name,
            targets=list(c.get("targets") or []),
            source_order=order,
            need=int(c.get("need", 5)),
        )
    checks = ChecksCfg(**(raw.get("checks") or {}))
    if not sources:
        raise ValueError("sources가 비어 있습니다")
    return Config(sources=sources, categories=categories, checks=checks)
