import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from .adapters.github import GitHub
from .adapters.hn import HN
from .adapters.rss import RSS
from .cluster import cluster
from .config import Config
from .digest import render, select
from .llm import LLMError, OpenRouter, apply
from .models import Item, RawItem
from .normalize import normalize
from .scoring import score_all
from .storage import Store


def ingest(
    config: Config, store: Store, only: str | None = None, since: datetime | None = None
) -> list[Item]:
    now = datetime.now(timezone.utc)
    start = since or (now - timedelta(hours=2))
    since_timestamp = int(start.timestamp())
    jobs: list[tuple[str, Callable[[], list[RawItem]]]] = []
    for name, factory in [
        ("hn_show", lambda: HN().fetch(since_timestamp)),
        (
            "github_search",
            lambda: GitHub(os.getenv("GITHUB_TOKEN")).fetch(
                int(config.sources["github_search"].get("min_stars", 10)),
                list(config.sources["github_search"].get("languages", [])),
                start,
            ),
        ),
        ("rss", lambda: RSS().fetch(list(config.sources["rss"].get("feeds", [])))),
    ]:
        settings = config.sources.get(name, {})
        if not settings.get("enabled", False) or (only and only != name):
            continue
        last = store.last_run(name)
        if (
            not only
            and last
            and now - last < timedelta(minutes=int(settings.get("interval_min", 60)))
        ):
            continue
        jobs.append((name, factory))
    items: list[Item] = []
    source_runs: list[tuple[str, str, int, str | None]] = []
    for source, fetch in jobs:
        try:
            fresh: list[Item] = []
            parse_failures = 0
            for raw in fetch():
                try:
                    fresh.append(normalize(raw))
                except Exception as exc:
                    parse_failures += 1
                    store.dead_letter(source, str(exc), raw.raw)
            items.extend(fresh)
            source_runs.append((source, "degraded" if parse_failures else "ok", len(fresh), None))
        except Exception as exc:
            error = str(exc)[:500]
            source_runs.append((source, "degraded", 0, error))
            store.dead_letter(source, error)
    result = score_all(cluster(items), config)
    try:
        store.save(result)
    except Exception as exc:
        storage_error = f"storage: {str(exc)[:450]}"
        for source, _, count, _ in source_runs:
            store.run(source, "degraded", count, storage_error)
        store.dead_letter(None, storage_error)
        raise
    for source, status, count, run_error in source_runs:
        store.run(source, status, count, run_error)
    store.prune(config.raw_retention_days)
    return result


def make_digest(
    config: Config, store: Store, output: str = "out/public", record: bool = True
) -> list[Item]:
    candidates = store.candidates(config.llm_candidate_limit)
    if candidates and os.getenv("OPENROUTER_API_KEY"):
        try:
            router = OpenRouter(
                min_context_length=config.llm_min_context_length,
                min_capability_score=config.llm_min_capability_score,
            )
            candidates = apply(candidates, router.rerank(candidates))
            print(f"OpenRouter selected free model: {router.selected_model}")
        except (LLMError, ValueError) as exc:
            print(f"LLM fallback: {exc}")
    chosen = select(candidates, config.digest_size, config.digest_max_size)
    render(chosen, output)
    if record:
        store.digest(chosen, datetime.now(timezone.utc))
    return chosen
