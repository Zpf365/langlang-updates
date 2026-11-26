from __future__ import annotations

import sqlite3
from pathlib import Path

from weimi_manager.db import Database


def test_old_analytics_outbox_is_migrated_before_index_creation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old-outbox.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE analytics_outbox (
            event_id TEXT PRIMARY KEY,
            dedupe_key TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            device_ref TEXT NOT NULL,
            store_ref TEXT NOT NULL DEFAULT '',
            batch_id TEXT NOT NULL,
            payload_protected BLOB NOT NULL,
            priority INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sent_at TEXT
        );
        INSERT INTO analytics_outbox (
            event_id, dedupe_key, event_type, schema_version, endpoint,
            occurred_at, device_ref, batch_id, payload_protected,
            next_attempt_at, created_at, updated_at
        ) VALUES (
            'event-1', 'dedupe-1', 'store.daily_metrics', 1,
            '/v1/report/store-daily', '2026-11-25T00:00:00+00:00',
            'device', 'batch', X'00', '2026-11-25T00:00:00+00:00',
            '2026-11-25T00:00:00+00:00',
            '2026-11-25T00:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()

    database = Database(path)
    with database.connect() as migrated:
        columns = {
            str(row[1])
            for row in migrated.execute(
                "PRAGMA table_info(analytics_outbox)"
            ).fetchall()
        }
        assert {
            "business_key",
            "aggregation",
            "revision",
            "last_error_category",
        }.issubset(columns)
        row = migrated.execute(
            """
            SELECT event_id, business_key, aggregation, revision
            FROM analytics_outbox WHERE event_id='event-1'
            """
        ).fetchone()
        assert tuple(row) == ("event-1", "", "event", 0)
        index = migrated.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='index' AND name='idx_analytics_outbox_business'
            """
        ).fetchone()
        assert index is not None
