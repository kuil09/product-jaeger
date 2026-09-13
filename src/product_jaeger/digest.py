import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Item

LEADERBOARD_MAX_ITEMS = 20


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


def _leaderboard_items(items: list[Item], maximum: int = LEADERBOARD_MAX_ITEMS) -> list[Item]:
    ranked = sorted(
        (item for item in items if item.llm_decision != "skip"),
        key=lambda item: (
            -item.final_score,
            -item.raw_score,
            -item.first_seen_at.timestamp(),
            item.id,
        ),
    )
    selected: list[Item] = []
    clusters: set[str] = set()
    for item in ranked:
        if item.cluster_id and item.cluster_id in clusters:
            continue
        selected.append(item)
        if item.cluster_id:
            clusters.add(item.cluster_id)
        if len(selected) >= min(maximum, LEADERBOARD_MAX_ITEMS):
            break
    return selected


def leaderboard_record(item: Item, published: datetime, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "title": item.title,
        "url": item.canonical_url,
        "source_url": item.source_url,
        "sources": item.sources or [item.source],
        "topics": item.llm_tags or item.topics,
        "summary": item.llm_summary or item.description[:280],
        "raw_score": round(item.raw_score, 4),
        "final_score": round(item.final_score, 4),
        "first_seen_at": item.first_seen_at.isoformat(),
        "published_at": published.isoformat(),
    }


def _leaderboard_records(items: list[Item], published: datetime) -> list[dict[str, Any]]:
    return [
        leaderboard_record(item, published, rank)
        for rank, item in enumerate(_leaderboard_items(items), start=1)
    ]


def _leaderboard_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "<p>No leaderboard entries in the latest digest.</p>"
    rows = "".join(
        "<tr>"
        f"<td>{record['rank']}</td>"
        f"<th scope='row'><a href='{html.escape(str(record['url']))}'>{html.escape(str(record['title']))}</a></th>"
        f"<td>{record['final_score']:.4f}</td>"
        f"<td>{html.escape(', '.join(map(str, record['sources'])))}</td>"
        f"<td>{html.escape(', '.join(map(str, record['topics'])))}</td>"
        f"<td><time datetime='{html.escape(str(record['first_seen_at']))}'>{html.escape(str(record['first_seen_at']))}</time></td>"
        "</tr>"
        for record in records
    )
    return (
        "<div style='overflow-x:auto'><table><thead><tr>"
        "<th scope='col'>Rank</th><th scope='col'>Product</th><th scope='col'>Final score</th>"
        "<th scope='col'>Sources</th><th scope='col'>Topics</th><th scope='col'>First seen</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _write_leaderboard(records: list[dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps({"items": records}, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_digest(
    items: list[Item],
    json_path: Path,
    html_path: Path,
    published: datetime,
    include_leaderboard: bool = False,
) -> None:
    records = [public_record(i, published) for i in items]
    json_path.write_text(
        json.dumps({"items": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cards = "".join(
        f'<article><h2><a href="{html.escape(str(r["url"]))}">{html.escape(str(r["title"]))}</a></h2><p>{html.escape(str(r["summary"]))}</p><small>{html.escape(str(r["sources"]))} · {r["final_score"]}</small></article>'
        for r in records
    )
    leaderboard = _leaderboard_records(items, published)
    leaderboard_section = (
        "<section aria-labelledby='leaderboard-title'><h2 id='leaderboard-title'>Current leaderboard</h2>"
        "<p>Latest digest candidates ranked by Product Jaeger final score."
        " <a href='leaderboard.json'>Download leaderboard JSON</a></p>"
        f"{_leaderboard_table(leaderboard)}</section>"
        if include_leaderboard
        else ""
    )
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Product Jaeger</title>"
        "<style>body{max-width:1100px;margin:40px auto;padding:0 16px;font:16px system-ui}"
        "article{padding:16px 0;border-bottom:1px solid #ddd}"
        "table{border-collapse:collapse;width:100%;min-width:760px}th,td{border-bottom:1px solid #ddd;padding:10px;text-align:left;vertical-align:top}"
        "thead{background:#f5f5f5}</style>"
        f"<h1>Product Jaeger</h1><p>Early signal digest · {html.escape(published.isoformat())}</p>"
        f"{leaderboard_section}"
        f"<section aria-labelledby='digest-title'><h2 id='digest-title'>Latest digest</h2>{cards}</section>",
        encoding="utf-8",
    )


def render(items: list[Item], output: str | Path, published: datetime | None = None) -> None:
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = published or datetime.now(timezone.utc)
    _write_digest(items, directory / "latest.json", directory / "index.html", timestamp, True)
    _write_leaderboard(_leaderboard_records(items, timestamp), directory / "leaderboard.json")


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
