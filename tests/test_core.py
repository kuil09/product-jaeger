import json
from dataclasses import replace
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import httpx
import pytest

from product_jaeger import cli, pipeline
from product_jaeger.adapters.github import GitHub
from product_jaeger.adapters.hn import HN
from product_jaeger.adapters.rss import RSS
from product_jaeger.cluster import cluster
from product_jaeger.config import Config
from product_jaeger.digest import render, render_archive, select
from product_jaeger.llm import LLMError, OpenRouter, rank_free_models, require_free_model
from product_jaeger.models import Item, RawItem
from product_jaeger.scoring import score_all
from product_jaeger.urls import canonicalize_url


def cfg() -> Config:
    return Config(
        15,
        20,
        40,
        90,
        True,
        ("hardware", "oss"),
        ("medium.com",),
        {},
        {
            "novelty": 0.28,
            "velocity": 0.22,
            "multi_source": 0.18,
            "technical_depth": 0.12,
            "topic_fit": 0.10,
            "anti_spam": 0.10,
        },
    )


def item(i: str, source: str = "hn_show") -> Item:
    now = datetime.now(timezone.utc)
    return Item(
        i,
        f"https://example.com/{i}",
        "Open hardware project",
        "A useful OSS hardware project",
        source,
        i,
        f"https://source/{i}",
        now,
        now,
        now,
        topics=["hardware"],
        metrics={"points": 30},
        velocity={"points_per_hour": 10},
    )


def test_url_and_cluster():
    assert (
        canonicalize_url("https://github.com/org/repo/tree/main?a=1")
        == "https://github.com/org/repo"
    )
    a, b = item("a"), item("b", "rss")
    b.canonical_url = a.canonical_url
    cluster([a, b])
    assert a.cluster_id == b.cluster_id
    assert a.sources == ["hn_show", "rss"]


def test_scoring_and_cap():
    items = [item(str(i)) for i in range(25)]
    score_all(items, cfg())
    items[0].llm_decision = "skip"
    assert len(select(items, 25, 20)) == 20 and items[0] not in select(items, 25, 20)


def test_free_model_and_projection(tmp_path):
    assert require_free_model("model:free") == "model:free"
    try:
        require_free_model("model")
    except ValueError:
        pass
    else:
        raise AssertionError("paid model accepted")
    render([item("x")], tmp_path)
    assert '"raw":' not in (tmp_path / "latest.json").read_text()
    leaderboard = json.loads((tmp_path / "leaderboard.json").read_text())
    assert leaderboard["items"][0]["rank"] == 1
    assert set(leaderboard["items"][0]) == {
        "rank",
        "title",
        "url",
        "source_url",
        "sources",
        "topics",
        "summary",
        "raw_score",
        "final_score",
        "first_seen_at",
        "published_at",
    }
    assert (tmp_path / "index.html").exists()
    assert "Current leaderboard" in (tmp_path / "index.html").read_text()
    render_archive([(datetime.now(timezone.utc), [item("y")])], tmp_path)
    assert list((tmp_path / "archive").glob("*.json"))
    assert (tmp_path / "archive.html").exists()
    archive_page = next((tmp_path / "archive").glob("*.html")).read_text()
    assert "Current leaderboard" not in archive_page


def test_leaderboard_ranks_deterministically_and_deduplicates(tmp_path):
    first = item("first")
    second = item("second", "rss")
    duplicate = item("duplicate", "github_search")
    first.id = "a"
    second.id = "b"
    first.cluster_id = "a"
    duplicate.cluster_id = first.id
    second.final_score = first.final_score = duplicate.final_score = 0.5
    second.raw_score = first.raw_score = duplicate.raw_score = 0.5
    second.first_seen_at = first.first_seen_at
    duplicate.first_seen_at = first.first_seen_at
    render([duplicate, second, first], tmp_path)
    records = json.loads((tmp_path / "leaderboard.json").read_text())["items"]
    assert [record["rank"] for record in records] == [1, 2]
    assert [record["title"] for record in records] == ["Open hardware project"] * 2
    assert records[0]["url"] < records[1]["url"]


def test_leaderboard_excludes_skip_and_caps_at_twenty(tmp_path):
    items = [item(str(index)) for index in range(25)]
    items[0].llm_decision = "skip"
    render(items, tmp_path)
    records = json.loads((tmp_path / "leaderboard.json").read_text())["items"]
    assert len(records) == 20
    assert all(record["rank"] == index for index, record in enumerate(records, start=1))
    assert all(record["url"] != items[0].canonical_url for record in records)


def test_empty_leaderboard_is_public_and_escaped(tmp_path):
    render([], tmp_path)
    assert json.loads((tmp_path / "leaderboard.json").read_text()) == {"items": []}
    assert "No leaderboard entries" in (tmp_path / "index.html").read_text()
    escaped = item("escaped")
    escaped.title = "<unsafe>"
    escaped.canonical_url = 'https://example.com/?q="unsafe"'
    render([escaped], tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert "&lt;unsafe&gt;" in page
    assert "&quot;unsafe&quot;" in page


def test_migration_is_packaged_without_drifting_from_repository_copy():
    repository_migration = Path(__file__).parents[1] / "migrations/001_initial.sql"
    packaged_migration = files("product_jaeger").joinpath("migrations/001_initial.sql")
    assert packaged_migration.read_text(encoding="utf-8") == repository_migration.read_text(
        encoding="utf-8"
    )


def model_metadata(
    model_id: str, parameters: list[str], context_length: int = 32768, score: str = "0"
) -> dict[str, object]:
    return {
        "id": model_id,
        "name": model_id,
        "context_length": context_length,
        "supported_parameters": parameters,
        "pricing": {"prompt": score, "completion": score},
        "top_provider": {"max_completion_tokens": 8192},
    }


def test_runtime_model_selection_filters_paid_and_weak_models():
    models = [
        model_metadata("paid/model", ["reasoning_effort", "structured_outputs"]),
        model_metadata("weak/model:free", ["temperature"]),
        model_metadata("capable/model:free", ["reasoning_effort", "structured_outputs"]),
    ]
    selected = rank_free_models(models)
    assert [candidate.id for candidate in selected] == ["capable/model:free"]


def test_runtime_model_selection_uses_catalog_and_falls_back_on_429():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        model_metadata(
                            "first/model:free", ["reasoning_effort", "structured_outputs"]
                        ),
                        model_metadata("second/model:free", ["reasoning", "response_format"]),
                    ]
                },
            )
        body = json.loads(request.content)
        calls.append((request.url.path, body["model"]))
        if body["model"] == "first/model:free":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"x": {"decision": "useful", "summary": "좋다", "tags": ["oss", "agent", "tool"]}}'
                        }
                    }
                ]
            },
        )

    router = OpenRouter(
        key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    results = router.rerank([item("x")])
    assert router.selected_model == "second/model:free"
    assert results["x"].decision == "useful"
    assert calls == [
        ("/api/v1/chat/completions", "first/model:free"),
        ("/api/v1/chat/completions", "second/model:free"),
    ]


def test_malformed_json_is_treated_as_model_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        model_metadata(
                            "broken/model:free", ["reasoning_effort", "structured_outputs"]
                        )
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    router = OpenRouter(
        key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(LLMError, match="all selected free OpenRouter models failed"):
        router.rerank([item("x")])


def test_source_adapters_parse_fixtures():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hn.algolia.com":
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "objectID": "1",
                            "title": "Show HN: Tiny Device",
                            "story_text": "A small local-first device.",
                            "url": "https://example.test/device?utm_source=hn",
                            "created_at_i": 1700000000,
                            "points": 12,
                            "num_comments": 3,
                        }
                    ]
                },
            )
        if request.url.host == "api.github.com":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "full_name": "octo/tiny-device",
                            "html_url": "https://github.com/octo/tiny-device",
                            "description": "Open hardware",
                            "created_at": "2026-09-12T00:00:00Z",
                            "stargazers_count": 22,
                            "forks_count": 2,
                            "topics": ["hardware"],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            content=b"""<rss><channel><item><guid>rss-1</guid><title>RSS Device</title><link>https://example.test/rss</link><description>Feed item</description></item></channel></rss>""",
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hn_items = HN(client).fetch(1690000000)
    github_items = GitHub(client=client).fetch(1, ["python"])
    rss_items = RSS(client).fetch(["https://feed.test/rss.xml"])
    assert hn_items[0].source_id == "1"
    assert github_items[0].source_id == "octo/tiny-device"
    assert rss_items[0].title == "RSS Device"


def test_conservative_clustering_and_score_range():
    first = item("first")
    second = item("second", "rss")
    second.title = "Open hardware project"
    second.canonical_url = first.canonical_url
    third = item("third", "github_search")
    third.title = "Unrelated cooking notes"
    third.canonical_url = "https://other.test/cooking"
    clustered = cluster([first, second, third])
    assert clustered[0].cluster_id == clustered[1].cluster_id
    assert clustered[2].cluster_id != clustered[0].cluster_id
    scored = score_all(clustered, cfg())
    assert all(0 <= value.raw_score <= 1 for value in scored)


def test_digest_is_recorded_before_telegram_delivery(monkeypatch):
    events: list[str] = []

    class FakeStore:
        def __init__(self, dsn: str) -> None:
            assert dsn == "postgres://test"

        def init(self) -> None:
            events.append("init")

        def digest(self, items: list[Item], published: datetime) -> None:
            events.append("digest")

    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://test")
    monkeypatch.setattr(cli, "Store", FakeStore)
    monkeypatch.setattr(cli, "load_config", lambda _: replace(cfg(), dry_run=False))
    monkeypatch.setattr(cli, "make_digest", lambda *args, **kwargs: [item("telegram")])

    def failing_send(items: list[Item]) -> None:
        events.append("send")
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(cli, "send", failing_send)
    with pytest.raises(RuntimeError, match="telegram unavailable"):
        cli.main(["digest", "--send"])
    assert events == ["init", "digest", "send"]


def test_ingest_records_source_run_after_storage(monkeypatch):
    events: list[str] = []

    class FakeStore:
        def last_run(self, source: str) -> None:
            return None

        def save(self, items: list[Item]) -> None:
            events.append("save")

        def run(self, source: str, status: str, count: int = 0, error: str | None = None) -> None:
            events.append("run")

        def dead_letter(self, source: str | None, error: str, payload: dict | None = None) -> None:
            events.append("dead_letter")

        def prune(self, days: int) -> None:
            events.append("prune")

    class FakeHN:
        def fetch(self, since: int) -> list[RawItem]:
            return [
                RawItem(
                    "hn_show",
                    "fixture-1",
                    "https://news.ycombinator.com/item?id=fixture-1",
                    "Fixture product",
                    "A local-first tool",
                    "https://example.test/fixture-1",
                )
            ]

    config = replace(cfg(), sources={"hn_show": {"enabled": True, "interval_min": 15}})
    monkeypatch.setattr(pipeline, "HN", FakeHN)
    pipeline.ingest(config, FakeStore())
    assert events[:2] == ["save", "run"]
