from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from product_jaeger import cli, pipeline, storage
from product_jaeger.config import load_config
from product_jaeger.models import RawItem
from product_jaeger.storage import Store

NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / "config/config.yml")


@pytest.fixture
def frozen_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(pipeline, "datetime", Clock)


@pytest.fixture
def store():
    result = Mock(spec=Store)
    result.last_runs.return_value = {}
    return result


@pytest.fixture
def adapters(monkeypatch):
    results = {}
    for name, source in [("HN", "hn_show"), ("GitHub", "github_search"), ("RSS", "rss")]:
        adapter = Mock()
        adapter.fetch.return_value = [
            RawItem(source, "fixture", "https://example.test/fixture", "Fixture product")
        ]
        monkeypatch.setattr(pipeline, name, Mock(return_value=adapter))
        results[source] = adapter
    return results


@pytest.mark.parametrize("command", ["init-db", "ingest", "digest", "publish"])
def test_schema_initialization_is_explicit(command, config, monkeypatch):
    db = Mock(spec=Store)
    db.digest_history.return_value = []
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://test")
    monkeypatch.setattr(cli, "Store", Mock(return_value=db))
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "ingest", Mock(return_value=[]))
    monkeypatch.setattr(cli, "make_digest", Mock(return_value=[]))
    monkeypatch.setattr(cli, "render_archive", Mock())

    assert cli.main([command]) == 0
    assert db.init.call_count == (1 if command == "init-db" else 0)


def test_reads_gates_once_and_records_after_save(config, frozen_clock, store, adapters):
    pipeline.ingest(config, store)

    store.last_runs.assert_called_once_with(["hn_show", "github_search", "rss"])
    assert [call[0] for call in store.mock_calls] == ["last_runs", "save", "record_runs", "prune"]
    store.record_runs.assert_called_once_with(
        [
            ("hn_show", "ok", 1, None),
            ("github_search", "ok", 1, None),
            ("rss", "ok", 1, None),
        ]
    )


def test_mixed_intervals_include_boundary_and_missing_source(config, frozen_clock, store, adapters):
    store.last_runs.return_value = {
        "hn_show": NOW - timedelta(minutes=15),
        "github_search": NOW - timedelta(minutes=59),
    }
    pipeline.ingest(config, store)

    adapters["hn_show"].fetch.assert_called_once()
    adapters["github_search"].fetch.assert_not_called()
    adapters["rss"].fetch.assert_called_once()
    assert [row[0] for row in store.record_runs.call_args.args[0]] == ["hn_show", "rss"]


def test_no_due_sources_performs_no_writes(config, frozen_clock, store, adapters):
    store.last_runs.return_value = {name: NOW for name in config.sources}
    assert pipeline.ingest(config, store) == []

    assert [call[0] for call in store.mock_calls] == ["last_runs"]
    for adapter in adapters.values():
        adapter.fetch.assert_not_called()


def test_no_enabled_sources_never_touches_database(config, frozen_clock, store, adapters):
    config = replace(config, sources={"hn_show": {"enabled": False}})
    assert pipeline.ingest(config, store) == []
    assert store.mock_calls == []


def test_targeted_run_skips_gate_reads(config, frozen_clock, store, adapters):
    store.last_runs.return_value = {"hn_show": NOW}
    pipeline.ingest(config, store, only="hn_show")

    store.last_runs.assert_not_called()
    adapters["hn_show"].fetch.assert_called_once()
    adapters["github_search"].fetch.assert_not_called()
    adapters["rss"].fetch.assert_not_called()
    store.record_runs.assert_called_once_with([("hn_show", "ok", 1, None)])


def test_targeted_run_still_respects_disabled_source(config, frozen_clock, store, adapters):
    config = replace(config, sources={"hn_show": {"enabled": False}})
    assert pipeline.ingest(config, store, only="hn_show") == []
    assert store.mock_calls == []
    adapters["hn_show"].fetch.assert_not_called()


def test_outage_does_not_expand_normal_window(config, frozen_clock, store, adapters):
    store.last_runs.return_value = {name: NOW - timedelta(days=30) for name in config.sources}
    pipeline.ingest(config, store)

    start = NOW - timedelta(hours=2)
    adapters["hn_show"].fetch.assert_called_once_with(int(start.timestamp()))
    assert adapters["github_search"].fetch.call_args.args[-1] == start


def test_explicit_since_is_still_supported(config, frozen_clock, store, adapters):
    start = NOW - timedelta(days=1)
    pipeline.ingest(config, store, only="hn_show", since=start)
    adapters["hn_show"].fetch.assert_called_once_with(int(start.timestamp()))


def test_save_failure_records_degraded_batch_and_reraises(config, frozen_clock, store, adapters):
    store.save.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        pipeline.ingest(config, store)

    assert [call[0] for call in store.mock_calls] == [
        "last_runs",
        "save",
        "record_runs",
        "dead_letter",
    ]
    store.record_runs.assert_called_once_with(
        [
            (name, "degraded", 1, "storage: database unavailable")
            for name in ["hn_show", "github_search", "rss"]
        ]
    )
    store.prune.assert_not_called()


def test_source_failure_is_recorded_as_degraded(config, frozen_clock, store, adapters):
    adapters["hn_show"].fetch.side_effect = RuntimeError("source unavailable")
    pipeline.ingest(config, store)
    rows = store.record_runs.call_args.args[0]
    assert rows[0] == ("hn_show", "degraded", 0, "source unavailable")
    assert all(row[1] == "ok" for row in rows[1:])


def test_batch_gate_query_uses_one_short_lived_connection(monkeypatch):
    connect = Mock()
    db = connect.return_value.__enter__ = Mock(return_value=Mock())
    connect.return_value.__exit__ = Mock(return_value=False)
    connection = db.return_value
    connection.execute.return_value.fetchall.return_value = [("hn_show", NOW), ("rss", None)]
    monkeypatch.setattr(storage.psycopg, "connect", connect)

    assert Store("postgres://test").last_runs(["hn_show", "rss"]) == {"hn_show": NOW}
    connect.assert_called_once_with("postgres://test")
    sql, params = connection.execute.call_args.args
    assert "GROUP BY source" in sql
    assert params == (["hn_show", "rss"],)
    connect.return_value.__exit__.assert_called_once_with(None, None, None)


def test_batch_logs_use_one_connection_and_preserve_rows(monkeypatch):
    from unittest.mock import MagicMock

    connect = MagicMock()
    monkeypatch.setattr(storage.psycopg, "connect", connect)
    rows = [("hn_show", "ok", 2, None), ("rss", "degraded", 0, "unavailable")]
    Store("postgres://test").record_runs(rows)

    connect.assert_called_once_with("postgres://test")
    cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    assert cursor.executemany.call_args.args[1] == rows
    connect.return_value.__exit__.assert_called_once_with(None, None, None)


def test_empty_storage_batches_never_connect(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(storage.psycopg, "connect", connect)
    db = Store("postgres://test")
    assert db.last_runs([]) == {}
    db.record_runs([])
    connect.assert_not_called()


def test_workflows_keep_bootstrap_explicit_and_ingestion_serial():
    # BaseLoader preserves the YAML 'on' key rather than treating it as a bool.
    workflows = {
        name: yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
        for name in ["ingest.yml", "manual-run.yml"]
    }
    scheduled = workflows["ingest.yml"]
    manual = workflows["manual-run.yml"]
    assert scheduled["on"]["schedule"] == [{"cron": "*/15 * * * *"}]
    assert not any(
        step.get("run") == "product-jaeger init-db" for step in scheduled["jobs"]["ingest"]["steps"]
    )
    setup = [
        step
        for step in manual["jobs"]["run"]["steps"]
        if step.get("run") == "product-jaeger init-db"
    ]
    assert len(setup) == 1 and setup[0]["if"] == "${{ inputs.initialize_database }}"
    assert manual["on"]["workflow_dispatch"]["inputs"]["initialize_database"]["default"] == "false"
    assert manual["concurrency"] == scheduled["concurrency"]


def test_save_fetches_only_latest_identities_before_preserving_item_ids(monkeypatch):
    from unittest.mock import MagicMock

    from product_jaeger.normalize import normalize

    connect = MagicMock()
    monkeypatch.setattr(storage.psycopg, "connect", connect)
    db = connect.return_value.__enter__.return_value
    # PostgreSQL returns only one (latest) observation per matching source pair.
    db.execute.return_value.fetchall.side_effect = [
        [("hn-existing", "hn_show", "shared-id"), ("rss-existing", "rss", "shared-id")],
        [],
        [],
    ]
    items = [
        normalize(RawItem(source, "shared-id", f"https://example.test/{source}", source))
        for source in ["hn_show", "rss"]
    ]
    Store("postgres://test").save(items)

    sql, params = db.execute.call_args_list[0].args
    assert "SELECT DISTINCT ON (source,source_id) item_id,source,source_id" in sql
    assert "ORDER BY source,source_id,observed_at DESC,id DESC" in sql
    assert set(params[0]) == {"hn_show", "rss"}
    assert params[1] == ["shared-id"]
    cursor = db.cursor.return_value.__enter__.return_value
    item_rows = cursor.executemany.call_args_list[0].args[1]
    observation_rows = cursor.executemany.call_args_list[1].args[1]
    assert [row[0] for row in item_rows] == ["hn-existing", "rss-existing"]
    assert [row[0] for row in observation_rows] == ["hn-existing", "rss-existing"]
