from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.infrastructure import migrations as migration_module
from weimi_manager.dongxiao.infrastructure.migrations import (
    migrate_dongxiao_schema,
)
from weimi_manager.sales_related.migrations import (
    migrate_sales_related_schema,
)

REQUIRED_TABLES = {
    "dongxiao_schema_migrations",
    "dongxiao_products",
    "dongxiao_skus",
    "dongxiao_product_snapshots",
    "dongxiao_product_pools",
    "dongxiao_product_pool_items",
    "dongxiao_tasks",
    "dongxiao_task_items",
    "dongxiao_task_attempts",
    "dongxiao_order_refs",
    "dongxiao_order_status_snapshots",
    "dongxiao_aftersale_cases",
    "dongxiao_approvals",
    "dongxiao_audit_events",
}


def test_dongxiao_migration_creates_isolated_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dongxiao.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-11');
            """
        )
        conn.commit()
        backups: list[str] = []

        changed = migrate_dongxiao_schema(
            conn,
            path,
            lambda label: backups.append(label)
            or path.with_suffix(".backup"),
        )

        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert changed is True
        assert REQUIRED_TABLES.issubset(tables)
        assert backups == ["pre-dongxiao-v1"]
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations"
        ).fetchall() == [(1,)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_dongxiao_migration_is_repeatable_and_preserves_legacy_sales_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "compatible.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-11');
            """
        )
        conn.commit()
        migrate_sales_related_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".sales.backup"),
        )
        conn.execute(
            """
            INSERT INTO sales_audit_logs(
                action, actor, task_id, device_id, decision,
                detail_json, created_at
            ) VALUES(
                'legacy_event', 'tester', 'task-old', '',
                'created', '{}', '2026-11-18T10:00:00+00:00'
            )
            """
        )
        conn.commit()
        old_checksum = conn.execute(
            """
            SELECT checksum FROM schema_migrations
            WHERE scope='sales_related' AND version=1
            """
        ).fetchone()[0]
        backups: list[str] = []
        backup = path.with_suffix(".dongxiao.backup")

        assert migrate_dongxiao_schema(
            conn,
            path,
            lambda label: backups.append(label) or backup,
        )
        assert not migrate_dongxiao_schema(
            conn,
            path,
            lambda label: backups.append(label) or backup,
        )

        assert backups == ["pre-dongxiao-v1"]
        assert conn.execute(
            "SELECT COUNT(*) FROM sales_audit_logs"
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT checksum FROM schema_migrations
            WHERE scope='sales_related' AND version=1
            """
        ).fetchone()[0] == old_checksum
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_schema_migrations"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_failed_dongxiao_migration_rolls_back_all_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "rollback.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-11');
            """
        )
        conn.commit()
        monkeypatch.setattr(
            migration_module,
            "_migration_sql",
            lambda: """
                CREATE TABLE dongxiao_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE dongxiao_failure_probe (id INTEGER);
                INSERT INTO table_that_does_not_exist(id) VALUES(1);
            """,
        )

        with pytest.raises(sqlite3.OperationalError):
            migrate_dongxiao_schema(
                conn,
                path,
                lambda _label: path.with_suffix(".backup"),
            )

        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "dongxiao_failure_probe" not in tables
        assert "dongxiao_schema_migrations" not in tables
    finally:
        conn.close()


def test_database_initialization_applies_dongxiao_with_legacy_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "integrated.db"
    Database(path)

    conn = sqlite3.connect(path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert REQUIRED_TABLES.issubset(tables)
        assert "sales_tasks" in tables
        assert "sales_audit_logs" in tables
    finally:
        conn.close()
