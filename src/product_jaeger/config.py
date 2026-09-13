import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class Config:
    digest_size: int
    digest_max_size: int
    llm_candidate_limit: int
    raw_retention_days: int
    dry_run: bool
    topics: tuple[str, ...]
    ignore_domains: tuple[str, ...]
    sources: dict[str, dict[str, Any]]
    weights: dict[str, float]
    llm_min_context_length: int = 8192
    llm_min_capability_score: int = 7


def load_config(path: str | Path | None = None) -> Config:
    target = Path(path or os.getenv("RADAR_CONFIG") or "config/config.yml")
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    weights = {str(k): float(v) for k, v in raw.get("weights", {}).items()}
    expected = {"novelty", "velocity", "multi_source", "technical_depth", "topic_fit", "anti_spam"}
    if set(weights) != expected or abs(sum(weights.values()) - 1) > 0.001:
        raise ValueError("weights must contain the six scoring features and sum to 1")
    return Config(
        int(raw.get("digest_size", 15)),
        int(raw.get("digest_max_size", 20)),
        int(raw.get("llm_candidate_limit", 40)),
        int(raw.get("raw_retention_days", 90)),
        os.getenv("RADAR_DRY_RUN", str(raw.get("dry_run", True))).lower() in {"1", "true", "yes"},
        tuple(map(str, raw.get("topics", []))),
        tuple(map(str, raw.get("ignore_domains", []))),
        dict(raw.get("sources", {})),
        weights,
        int(raw.get("llm_min_context_length", 8192)),
        int(raw.get("llm_min_capability_score", 7)),
    )
