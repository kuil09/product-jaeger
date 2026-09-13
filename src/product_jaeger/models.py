from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RawItem:
    source: str
    source_id: str
    source_url: str
    title: str
    description: str = ""
    url: str = ""
    launched_at: datetime | None = None
    topics: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=now_utc)


@dataclass(slots=True)
class Item:
    id: str
    canonical_url: str
    title: str
    description: str
    source: str
    source_id: str
    source_url: str
    launched_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    topics: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    velocity: dict[str, float] = field(default_factory=dict)
    cluster_id: str | None = None
    sources: list[str] = field(default_factory=list)
    raw_score: float = 0.0
    llm_decision: str | None = None
    llm_summary: str | None = None
    llm_tags: list[str] = field(default_factory=list)
    final_score: float = 0.0
    status: str = "new"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RerankResult:
    decision: str
    summary: str
    tags: list[str]
    skip_reason: str | None = None
