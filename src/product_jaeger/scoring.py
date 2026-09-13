import math
from datetime import datetime, timezone

from .config import Config
from .models import Item

SPAM = ("#1 ai wrapper", "replace your team", "just another wrapper", "buy followers")


def score(item: Item, config: Config, source_count: int = 1, now: datetime | None = None) -> float:
    current = now or datetime.now(timezone.utc)
    age = max(0, (current - (item.launched_at or item.first_seen_at)).total_seconds() / 3600)
    novelty = math.exp(-age / 48)
    values = [v for k, v in item.velocity.items() if k.endswith("_per_hour")]
    values = values or [v for k, v in item.metrics.items() if k in {"points", "stars", "votes"}]
    velocity = min(1, math.log1p(max(values, default=0)) / math.log1p(100))
    multi = min(1, max(0, source_count - 1) / 2)
    depth = min(
        1,
        (0.45 if item.source == "github_search" else 0)
        + min(0.35, len(item.description) / 1000)
        + (0.2 if item.canonical_url else 0),
    )
    text = f"{item.title} {item.description} {' '.join(item.topics)}".lower()
    topic = min(1, sum(1 for t in config.topics if t.lower() in text) / 2)
    spam = (
        0
        if any(marker in text for marker in SPAM)
        else (0.35 if any(d in item.canonical_url for d in config.ignore_domains) else 1)
    )
    parts = {
        "novelty": novelty,
        "velocity": velocity,
        "multi_source": multi,
        "technical_depth": depth,
        "topic_fit": topic,
        "anti_spam": spam,
    }
    item.raw_score = sum(parts[key] * config.weights[key] for key in parts)
    if spam == 0:
        item.raw_score *= 0.5
    item.final_score = item.raw_score
    return item.raw_score


def score_all(items: list[Item], config: Config) -> list[Item]:
    groups: dict[str, set[str]] = {}
    for item in items:
        groups.setdefault(item.cluster_id or item.id, set()).add(item.source)
    for item in items:
        score(item, config, len(groups[item.cluster_id or item.id]))
    return sorted(items, key=lambda i: i.final_score, reverse=True)
