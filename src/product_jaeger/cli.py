import argparse
import os
from datetime import datetime, timezone

from .config import load_config
from .digest import render_archive
from .notify import send
from .pipeline import ingest, make_digest
from .storage import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    ingest_parser = sub.add_parser("ingest")
    ingest_parser.add_argument("--source", choices=["hn_show", "github_search", "rss"])
    ingest_parser.add_argument("--since", help="ISO-8601 start time for a manual replay")
    digest_parser = sub.add_parser("digest")
    digest_parser.add_argument("--output", default="out/public")
    digest_parser.add_argument("--send", action="store_true")
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--output", default="out/public")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    dsn = os.getenv("NEON_DATABASE_URL")
    if not dsn:
        raise SystemExit("NEON_DATABASE_URL is required")
    store = Store(dsn)
    store.init()
    if args.command == "init-db":
        print("database initialized")
        return 0
    if args.command == "ingest":
        since = _parse_since(args.since) if args.since else None
        print(f"ingested {len(ingest(config, store, args.source, since))} items")
        return 0
    if args.command == "publish":
        render_archive(store.digest_history(config.raw_retention_days), args.output)
        print("published latest digest")
        return 0
    selected = make_digest(config, store, args.output, record=False)
    store.digest(selected, datetime.now(timezone.utc))
    if args.send and not config.dry_run:
        send(selected)
    print(f"generated digest with {len(selected)} items")
    return 0


def _parse_since(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("--since must be a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
