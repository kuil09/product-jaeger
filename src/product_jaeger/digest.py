import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Item


def select(items: list[Item], size: int, maximum: int) -> list[Item]:
    selected: list[Item] = []
    clusters: set[str] = set()
    for item in sorted(items, key=lambda i: i.final_score, reverse=True):
        if item.llm_decision == "skip" or (item.cluster_id and item.cluster_id in clusters):
            continue
        selected.append(item)
        if item.cluster_id:
            clusters.add(item.cluster_id)
        if len(selected) >= min(size, maximum):
            break
    return selected


def public_record(item: Item, published: datetime) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "url": item.canonical_url,
        "source_url": item.source_url,
        "sources": item.sources or [item.source],
        "topics": item.llm_tags or item.topics,
        "summary": item.llm_summary or item.description[:280],
        "raw_score": round(item.raw_score, 4),
        "final_score": round(item.final_score, 4),
        "llm_decision": item.llm_decision,
        "first_seen_at": item.first_seen_at.isoformat(),
        "published_at": published.isoformat(),
    }


def _write_digest(items: list[Item], json_path: Path, html_path: Path, published: datetime) -> None:
    records = [public_record(i, published) for i in items]
    json_path.write_text(
        json.dumps({"items": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cards = "".join(
        f'<article><h2><a href="{html.escape(str(r["url"]))}">{html.escape(str(r["title"]))}</a></h2><p>{html.escape(str(r["summary"]))}</p><small>{html.escape(str(r["sources"]))} · {r["final_score"]}</small></article>'
        for r in records
    )
    html_path.write_text(
        f"<!doctype html><meta charset='utf-8'><title>Product Jaeger</title><style>body{{max-width:850px;margin:40px auto;font:16px system-ui}}article{{padding:16px 0;border-bottom:1px solid #ddd}}</style><h1>Product Jaeger</h1><p>Early signal digest · {published.isoformat()}</p>{cards}",
        encoding="utf-8",
    )


def render(items: list[Item], output: str | Path, published: datetime | None = None) -> None:
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = published or datetime.now(timezone.utc)
    _write_digest(items, directory / "latest.json", directory / "index.html", timestamp)


def render_archive(digests: list[tuple[datetime, list[Item]]], output: str | Path) -> None:
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    if not digests:
        render([], directory)
        return
    archive = directory / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    links: list[str] = []
    for published, items in digests:
        timestamp = published.astimezone(timezone.utc)
        slug = timestamp.strftime("%Y%m%dT%H%M%SZ")
        _write_digest(items, archive / f"{slug}.json", archive / f"{slug}.html", timestamp)
        links.append(
            f"<li><a href='archive/{slug}.html'>{timestamp.isoformat()}</a> ({len(items)} items)</li>"
        )
    latest_published, latest_items = digests[0]
    render(latest_items, directory, latest_published)
    (directory / "archive.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Product Jaeger archive</title>"
        "<h1>Product Jaeger archive</h1><ul>" + "".join(links) + "</ul>",
        encoding="utf-8",
    )
