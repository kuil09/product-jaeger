from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Item


class Store:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def init(self) -> None:
        path = Path(__file__).resolve().parents[2] / "migrations/001_initial.sql"
        with psycopg.connect(self.dsn) as db:
            db.execute(path.read_text(encoding="utf-8"))

    def last_run(self, source: str) -> datetime | None:
        with psycopg.connect(self.dsn) as db:
            row = db.execute(
                "SELECT max(created_at) FROM runs WHERE source=%s", (source,)
            ).fetchone()
        return row[0] if row and isinstance(row[0], datetime) else None

    def run(self, source: str, status: str, count: int = 0, error: str | None = None) -> None:
        with psycopg.connect(self.dsn) as db:
            db.execute(
                "INSERT INTO runs(source,status,item_count,error) VALUES(%s,%s,%s,%s)",
                (source, status, count, error),
            )

    def dead_letter(
        self, source: str | None, error: str, payload: dict[str, Any] | None = None
    ) -> None:
        with psycopg.connect(self.dsn) as db:
            db.execute(
                "INSERT INTO dead_letters(source,error,payload) VALUES(%s,%s,%s)",
                (source, error[:500], Jsonb(payload or {})),
            )

    def save(self, items: list[Item]) -> None:
        if not items:
            return
        with psycopg.connect(self.dsn) as db:
            source_values = list({i.source for i in items})
            source_id_values = list({i.source_id for i in items})
            identity_rows = db.execute(
                """
                SELECT item_id,source,source_id
                FROM source_observations
                WHERE source = ANY(%s) AND source_id = ANY(%s)
                ORDER BY observed_at DESC,id DESC
                """,
                (source_values, source_id_values),
            ).fetchall()
            identity_map: dict[tuple[str, str], str] = {}
            for row in identity_rows:
                identity_map.setdefault((str(row[1]), str(row[2])), str(row[0]))

            urls = list({i.canonical_url for i in items if i.canonical_url})
            canonical_map: dict[str, str] = {}
            if urls:
                canonical_rows = db.execute(
                    "SELECT id,canonical_url FROM items WHERE canonical_url = ANY(%s)",
                    (urls,),
                ).fetchall()
                canonical_map = {str(row[1]): str(row[0]) for row in canonical_rows}

            resolved: dict[tuple[str, str], str] = {}
            item_ids: list[str] = []
            for i in items:
                key = (i.source, i.source_id)
                item_id = resolved.get(key) or identity_map.get(key)
                if not item_id and i.canonical_url:
                    item_id = canonical_map.get(i.canonical_url)
                item_id = item_id or i.id
                resolved[key] = item_id
                item_ids.append(item_id)

            existing_ids = list(set(item_ids))
            previous_sources_map: dict[str, list[str]] = {}
            if existing_ids:
                previous_rows = db.execute(
                    "SELECT id,sources FROM items WHERE id = ANY(%s)",
                    (existing_ids,),
                ).fetchall()
                previous_sources_map = {
                    str(row[0]): [str(value) for value in (row[1] or [])] for row in previous_rows
                }

            item_values = []
            observation_values = []
            for i, item_id in zip(items, item_ids, strict=True):
                merged_sources = sorted(
                    set(previous_sources_map.get(item_id, [])) | set(i.sources or [i.source])
                )
                item_values.append(
                    (
                        item_id,
                        i.canonical_url,
                        i.title,
                        i.description,
                        i.source,
                        i.source_id,
                        i.source_url,
                        i.launched_at,
                        i.first_seen_at,
                        i.last_seen_at,
                        Jsonb(i.topics),
                        Jsonb(merged_sources),
                        Jsonb(i.metrics),
                        Jsonb(i.velocity),
                        i.cluster_id,
                        i.raw_score,
                        i.llm_decision,
                        i.llm_summary,
                        Jsonb(i.llm_tags),
                        i.final_score,
                        i.status,
                    )
                )
                observation_values.append(
                    (item_id, i.source, i.source_id, i.last_seen_at, Jsonb(i.metrics), Jsonb(i.raw))
                )

            with db.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO items(
                        id,canonical_url,title,description,source,source_id,source_url,launched_at,
                        first_seen_at,last_seen_at,topics,sources,metrics,velocity,cluster_id,raw_score,
                        llm_decision,llm_summary,llm_tags,final_score,status
                    ) VALUES(
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        last_seen_at=EXCLUDED.last_seen_at,topics=EXCLUDED.topics,
                        sources=EXCLUDED.sources,metrics=EXCLUDED.metrics,velocity=EXCLUDED.velocity,
                        cluster_id=EXCLUDED.cluster_id,raw_score=EXCLUDED.raw_score,
                        llm_decision=EXCLUDED.llm_decision,llm_summary=EXCLUDED.llm_summary,
                        llm_tags=EXCLUDED.llm_tags,final_score=EXCLUDED.final_score
                    """,
                    item_values,
                )
                cursor.executemany(
                    "INSERT INTO source_observations(item_id,source,source_id,observed_at,metrics,raw) VALUES(%s,%s,%s,%s,%s,%s)",
                    observation_values,
                )

    def candidates(self, limit: int) -> list[Item]:
        with psycopg.connect(self.dsn) as db:
            rows = db.execute(
                """
                SELECT i.id,i.canonical_url,i.title,i.description,i.source,i.source_id,i.source_url,
                       i.launched_at,i.first_seen_at,i.last_seen_at,i.topics,i.sources,i.metrics,
                       i.velocity,i.cluster_id,i.raw_score,i.llm_decision,i.llm_summary,i.llm_tags,
                       i.final_score,i.status
                FROM items i
                LEFT JOIN LATERAL (
                    SELECT d.item_id,d.payload
                    FROM digest_entries d
                    WHERE d.item_id=i.id
                    ORDER BY d.published_at DESC,d.id DESC
                    LIMIT 1
                ) last_digest ON TRUE
                WHERE i.status <> 'dismissed'
                  AND (
                    last_digest.item_id IS NULL
                    OR i.status <> 'sent'
                    OR COALESCE(jsonb_array_length(i.sources),0) >
                       COALESCE((last_digest.payload->>'source_count')::integer,0)
                    OR i.final_score >= COALESCE((last_digest.payload->>'final_score')::double precision,0) + 0.10
                    OR EXISTS (
                        SELECT 1
                        FROM jsonb_each_text(COALESCE(i.metrics,'{}'::jsonb)) current_metric
                        WHERE (last_digest.payload->'metrics') ? current_metric.key
                          AND current_metric.value::double precision >=
                              (last_digest.payload->'metrics'->>current_metric.key)::double precision * 1.25 + 1
                    )
                  )
                ORDER BY i.final_score DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [_item(row) for row in rows]

    def latest_digest(self, limit: int = 20) -> list[Item]:
        with psycopg.connect(self.dsn) as db:
            rows = db.execute(
                "SELECT i.id,i.canonical_url,i.title,i.description,i.source,i.source_id,i.source_url,i.launched_at,i.first_seen_at,i.last_seen_at,i.topics,i.sources,i.metrics,i.velocity,i.cluster_id,i.raw_score,i.llm_decision,i.llm_summary,i.llm_tags,i.final_score,i.status FROM items i JOIN digest_entries d ON d.item_id=i.id WHERE d.published_at=(SELECT max(published_at) FROM digest_entries) ORDER BY d.id LIMIT %s",
                (limit,),
            ).fetchall()
        return [_item(row) for row in rows]

    def digest_history(self, days: int = 90) -> list[tuple[datetime, list[Item]]]:
        with psycopg.connect(self.dsn) as db:
            rows = db.execute(
                """
                SELECT d.published_at,
                       i.id,i.canonical_url,i.title,i.description,i.source,i.source_id,i.source_url,
                       i.launched_at,i.first_seen_at,i.last_seen_at,i.topics,i.sources,i.metrics,
                       i.velocity,i.cluster_id,i.raw_score,i.llm_decision,i.llm_summary,i.llm_tags,
                       i.final_score,i.status
                FROM digest_entries d
                JOIN items i ON i.id=d.item_id
                WHERE d.published_at >= now() - (%s * interval '1 day')
                ORDER BY d.published_at DESC,d.id DESC
                """,
                (days,),
            ).fetchall()
        grouped: dict[datetime, list[Item]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(_item(row[1:]))
        return list(grouped.items())

    def digest(self, items: list[Item], published: datetime) -> None:
        with psycopg.connect(self.dsn) as db:
            for i in items:
                db.execute(
                    "INSERT INTO digest_entries(item_id,published_at,payload) VALUES(%s,%s,%s)",
                    (
                        i.id,
                        published,
                        Jsonb(
                            {
                                "final_score": i.final_score,
                                "source_count": len(i.sources or [i.source]),
                                "metrics": i.metrics,
                                "llm_decision": i.llm_decision,
                                "llm_summary": i.llm_summary,
                                "llm_tags": i.llm_tags,
                            }
                        ),
                    ),
                )
                db.execute(
                    """
                    UPDATE items
                    SET final_score=%s,llm_decision=%s,llm_summary=%s,llm_tags=%s,status='sent'
                    WHERE id=%s
                    """,
                    (i.final_score, i.llm_decision, i.llm_summary, Jsonb(i.llm_tags), i.id),
                )

    def prune(self, days: int) -> None:
        with psycopg.connect(self.dsn) as db:
            db.execute(
                "DELETE FROM source_observations WHERE observed_at < now() - (%s * interval '1 day')",
                (days,),
            )


def _item(row: tuple[Any, ...]) -> Item:
    return Item(
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
        row[6],
        row[7],
        row[8],
        row[9],
        row[10] or [],
        row[12] or {},
        row[13] or {},
        row[14],
        row[11] or [],
        row[15] or 0,
        row[16],
        row[17],
        row[18] or [],
        row[19] or 0,
        row[20],
    )
