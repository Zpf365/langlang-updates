from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import Database
from .adapter import AnalyticsEvent
from .privacy import payload_size


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="seconds")


class AnalyticsOutbox:
    def __init__(
        self,
        db: Database,
        *,
        max_queue_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.db = db
        self.max_queue_bytes = max(1024 * 1024, int(max_queue_bytes))

    def enqueue(self, event: AnalyticsEvent) -> bool:
        now = _stamp()
        payload = dict(event.payload)
        revision = event.revision
        with self.db.connect() as conn:
            if event.aggregation == "counter":
                current = conn.execute(
                    """
                    SELECT use_count, task_count, error_count, count
                    FROM analytics_daily_counters
                    WHERE business_key=?
                    """,
                    (event.business_key,),
                ).fetchone()
                use_count = int(current["use_count"] or 0) if current else 0
                task_count = int(current["task_count"] or 0) if current else 0
                error_count = int(current["error_count"] or 0) if current else 0
                count = int(current["count"] or 0) if current else 0
                use_count += int(payload.get("use_count") or 0)
                task_count += int(payload.get("task_count") or 0)
                error_count += int(payload.get("error_count") or 0)
                count += int(payload.get("count") or 0)
                payload.update(
                    {
                        "use_count": use_count,
                        "task_count": task_count,
                        "error_count": error_count,
                    }
                )
                if event.event_type == "error.summary":
                    payload["count"] = count
                conn.execute(
                    """
                    INSERT INTO analytics_daily_counters (
                        business_key, use_count, task_count,
                        error_count, count, revision, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(business_key) DO UPDATE SET
                        use_count=excluded.use_count,
                        task_count=excluded.task_count,
                        error_count=excluded.error_count,
                        count=excluded.count,
                        revision=excluded.revision,
                        updated_at=excluded.updated_at
                    """,
                    (
                        event.business_key,
                        use_count,
                        task_count,
                        error_count,
                        count,
                        revision,
                        now,
                    ),
                )
            protected = self.db.protector.protect(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            size_bytes = payload_size(payload)
            if event.aggregation in {"latest", "counter"}:
                pending = conn.execute(
                    """
                    SELECT event_id FROM analytics_outbox
                    WHERE business_key=? AND status='pending'
                    ORDER BY revision DESC
                    """,
                    (event.business_key,),
                ).fetchall()
                if pending:
                    keep_id = str(pending[0]["event_id"])
                    duplicate_ids = [
                        str(row["event_id"])
                        for row in pending[1:]
                    ]
                    conn.execute(
                        """
                        UPDATE analytics_outbox
                        SET event_id=?, dedupe_key=?, event_type=?,
                            schema_version=?, endpoint=?, occurred_at=?,
                            device_ref=?, store_ref=?, batch_id=?,
                            payload_protected=?, priority=?,
                            aggregation=?, revision=?, retry_count=0,
                            last_error_category='', next_attempt_at=?,
                            size_bytes=?, updated_at=?
                        WHERE event_id=?
                        """,
                        (
                            event.event_id,
                            event.dedupe_key,
                            event.event_type,
                            event.schema_version,
                            event.endpoint,
                            event.occurred_at,
                            event.device_ref,
                            event.store_ref,
                            event.batch_id,
                            protected,
                            event.priority,
                            event.aggregation,
                            revision,
                            now,
                            size_bytes,
                            now,
                            keep_id,
                        ),
                    )
                    if duplicate_ids:
                        placeholders = ",".join("?" for _ in duplicate_ids)
                        conn.execute(
                            f"DELETE FROM analytics_outbox "
                            f"WHERE event_id IN ({placeholders})",
                            duplicate_ids,
                        )
                    return True
            total = int(
                conn.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) FROM analytics_outbox"
                ).fetchone()[0]
                or 0
            )
            if total + size_bytes > self.max_queue_bytes:
                self._prune_with_connection(conn, make_room=size_bytes)
                total = int(
                    conn.execute(
                        "SELECT COALESCE(SUM(size_bytes), 0) FROM analytics_outbox"
                    ).fetchone()[0]
                    or 0
                )
                if total + size_bytes > self.max_queue_bytes:
                    return False
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO analytics_outbox (
                    event_id, dedupe_key, business_key, event_type, schema_version,
                    endpoint, occurred_at, device_ref, store_ref,
                    batch_id, payload_protected, priority, aggregation, revision, status,
                    retry_count, last_error_category, next_attempt_at,
                    size_bytes, created_at, updated_at, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                          0, '', ?, ?, ?, ?, NULL)
                """,
                (
                    event.event_id,
                    event.dedupe_key,
                    event.business_key,
                    event.event_type,
                    event.schema_version,
                    event.endpoint,
                    event.occurred_at,
                    event.device_ref,
                    event.store_ref,
                    event.batch_id,
                    protected,
                    event.priority,
                    event.aggregation,
                    revision,
                    now,
                    size_bytes,
                    now,
                    now,
                ),
            )
            return cursor.rowcount > 0

    def pending_batch(
        self,
        *,
        limit: int = 100,
        max_bytes: int = 256 * 1024,
    ) -> list[dict[str, Any]]:
        now = _stamp()
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, dedupe_key, event_type, schema_version,
                       endpoint, occurred_at, device_ref, store_ref,
                       batch_id, payload_protected, priority, aggregation,
                       revision, retry_count,
                       size_bytes, created_at
                FROM analytics_outbox
                WHERE status='pending' AND next_attempt_at<=?
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
                """,
                (now, max(1, min(100, int(limit)))),
            ).fetchall()
        output: list[dict[str, Any]] = []
        total = 0
        for row in rows:
            item = dict(row)
            size = int(item.get("size_bytes") or 0)
            if output and total + size > max_bytes:
                break
            if size > max_bytes:
                self.drop(
                    [str(item["event_id"])],
                    error_category="payload_too_large",
                )
                continue
            total += size
            output.append(item)
        return output

    def payload(self, row: dict[str, Any]) -> dict[str, Any]:
        protected = row.get("payload_protected")
        if not protected:
            return {}
        try:
            payload = json.loads(self.db.protector.unprotect(protected))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def event_envelope(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": str(row.get("event_id") or ""),
            "event_type": str(row.get("event_type") or ""),
            "schema_version": int(row.get("schema_version") or 1),
            "occurred_at": str(row.get("occurred_at") or ""),
            "device_ref": str(row.get("device_ref") or ""),
            "store_ref": str(row.get("store_ref") or ""),
            "batch_id": str(row.get("batch_id") or ""),
            "revision": int(row.get("revision") or 0),
            "payload": self.payload(row),
        }

    def claim(self, event_ids: list[str]) -> None:
        ids = [str(value) for value in event_ids if value]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        now = _stamp()
        with self.db.connect() as conn:
            conn.execute(
                f"""
                UPDATE analytics_outbox
                SET status='sending', updated_at=?
                WHERE event_id IN ({placeholders})
                  AND status='pending'
                """,
                (now, *ids),
            )

    def mark_sent(self, event_ids: list[str]) -> None:
        ids = [str(value) for value in event_ids if value]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        now = _stamp()
        with self.db.connect() as conn:
            conn.execute(
                f"""
                UPDATE analytics_outbox
                SET status='sent', sent_at=?, updated_at=?,
                    last_error_category=''
                WHERE event_id IN ({placeholders})
                """,
                (now, now, *ids),
            )

    def release(
        self,
        rows: list[dict[str, Any]],
        *,
        error_category: str,
    ) -> None:
        if not rows:
            return
        with self.db.connect() as conn:
            for row in rows:
                retry = int(row.get("retry_count") or 0) + 1
                priority = int(row.get("priority") or 1)
                max_retries = 8 if priority == 1 else 5
                if retry > max_retries and priority > 0:
                    conn.execute(
                        """
                        UPDATE analytics_outbox
                        SET status='dropped', retry_count=?,
                            last_error_category=?, updated_at=?
                        WHERE event_id=?
                        """,
                        (
                            retry,
                            str(error_category)[:120],
                            _stamp(),
                            str(row.get("event_id") or ""),
                        ),
                    )
                    continue
                delay = min(1800, 30 * (2 ** max(0, retry - 1)))
                conn.execute(
                    """
                    UPDATE analytics_outbox
                    SET status='pending', retry_count=?,
                        last_error_category=?, next_attempt_at=?,
                        updated_at=?
                    WHERE event_id=?
                    """,
                    (
                        retry,
                        str(error_category)[:120],
                        _stamp(_utc_now() + timedelta(seconds=delay)),
                        _stamp(),
                        str(row.get("event_id") or ""),
                    ),
                )

    def drop(
        self,
        event_ids: list[str],
        *,
        error_category: str,
    ) -> None:
        ids = [str(value) for value in event_ids if value]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self.db.connect() as conn:
            conn.execute(
                f"""
                UPDATE analytics_outbox
                SET status='dropped', last_error_category=?, updated_at=?
                WHERE event_id IN ({placeholders})
                """,
                (str(error_category)[:120], _stamp(), *ids),
            )

    def _prune_with_connection(self, conn, *, make_room: int) -> None:
        now = _utc_now()
        low_cutoff = _stamp(now - timedelta(days=7))
        server_cutoff = _stamp(now - timedelta(days=90))
        conn.execute(
            """
            DELETE FROM analytics_outbox
            WHERE priority=2 AND occurred_at<?
            """,
            (low_cutoff,),
        )
        conn.execute(
            """
            DELETE FROM analytics_outbox
            WHERE status='sent' AND occurred_at<?
            """,
            (server_cutoff,),
        )
        total = int(
            conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM analytics_outbox"
            ).fetchone()[0]
            or 0
        )
        if total + make_room <= self.max_queue_bytes:
            return
        excess = max(
            0,
            total + make_room - self.max_queue_bytes,
        )
        removed = 0
        rows = conn.execute(
            """
            SELECT event_id, size_bytes FROM analytics_outbox
            WHERE priority>0
            ORDER BY priority DESC, occurred_at ASC
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "DELETE FROM analytics_outbox WHERE event_id=?",
                (str(row["event_id"]),),
            )
            removed += int(row["size_bytes"] or 0)
            if removed >= excess:
                break

    def prune(self) -> None:
        with self.db.connect() as conn:
            self._prune_with_connection(conn, make_room=0)

    def stats(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count,
                       COALESCE(SUM(size_bytes), 0) AS bytes
                FROM analytics_outbox
                GROUP BY status
                """
            ).fetchall()
        by_status = {
            str(row["status"]): {
                "count": int(row["count"] or 0),
                "bytes": int(row["bytes"] or 0),
            }
            for row in rows
        }
        return {
            "by_status": by_status,
            "event_count": sum(item["count"] for item in by_status.values()),
            "queue_bytes": sum(item["bytes"] for item in by_status.values()),
            "max_queue_bytes": self.max_queue_bytes,
        }

    def diagnostics(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_type, status, retry_count,
                       last_error_category, next_attempt_at, updated_at
                FROM analytics_outbox
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "event_type": str(row["event_type"]),
                "status": str(row["status"]),
                "retry_count": int(row["retry_count"] or 0),
                "error_category": str(row["last_error_category"] or ""),
                "next_attempt_at": str(row["next_attempt_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ]
