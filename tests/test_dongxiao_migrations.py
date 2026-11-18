from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.dongxiao.infrastructure import migrations as migration_module
from weimi_manager.dongxiao.infrastructure.migrations import (
    DONGXIAO_V1_CHECKSUM,
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
    "dongxiao_product_imports",
    "dongxiao_product_pool_events",
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
        assert backups == ["pre-dongxiao-v2"]
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations"
        ).fetchall() == [(1,), (2,)]
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

        assert backups == ["pre-dongxiao-v2"]
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
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_failed_v2_migration_rolls_back_only_v2_and_keeps_v1_complete(
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
            "_migration_v2_sql",
            lambda: """
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
        assert "dongxiao_schema_migrations" in tables
        assert "dongxiao_products" in tables
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations"
        ).fetchall() == [(1,)]
    finally:
        conn.close()


def test_v1_checksum_is_frozen_for_published_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V1_CHECKSUM


def test_migration_stops_before_writes_for_backup_space_or_nested_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "guards.db"
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

        def failed_backup(_label: str) -> Path:
            raise OSError("backup unavailable")

        with pytest.raises(OSError):
            migrate_dongxiao_schema(conn, path, failed_backup)
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name LIKE 'dongxiao_%'
            """
        ).fetchone()[0] == 0

        monkeypatch.setattr(
            migration_module.shutil,
            "disk_usage",
            lambda _path: SimpleNamespace(free=0),
        )
        with pytest.raises(sqlite3.OperationalError):
            migrate_dongxiao_schema(
                conn,
                path,
                lambda _label: path.with_suffix(".backup"),
            )
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name LIKE 'dongxiao_%'
            """
        ).fetchone()[0] == 0

        monkeypatch.undo()
        conn.execute("BEGIN")
        with pytest.raises(sqlite3.OperationalError):
            migrate_dongxiao_schema(
                conn,
                path,
                lambda _label: path.with_suffix(".backup"),
            )
        conn.rollback()
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name LIKE 'dongxiao_%'
            """
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_v2_normalized_keys_reject_blank_and_duplicate_whitespace_sku(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unique.db"
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
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".backup"),
        )
        conn.execute(
            """
            INSERT INTO dongxiao_products(
                store_id, store_app_id, product_id, title, state,
                min_price_cents, stock_total, observed_at,
                created_at, updated_at
            ) VALUES(1, 'wx-store-11', 'prod-1', '商品', 'active',
                     100, 10, '2026-11-18T10:00:00+00:00',
                     '2026-11-18T10:00:00+00:00',
                     '2026-11-18T10:00:00+00:00')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO dongxiao_skus(
                    store_id, store_app_id, product_id, sku_id, price_cents,
                    stock, observed_at, created_at, updated_at
                ) VALUES(1, 'wx-store-11', 'prod-1', '   ', 100,
                         10, '2026-11-18T10:00:00+00:00',
                         '2026-11-18T10:00:00+00:00',
                         '2026-11-18T10:00:00+00:00')
                """
            )
        conn.execute(
            """
            INSERT INTO dongxiao_skus(
                store_id, store_app_id, product_id, sku_id, price_cents,
                stock, observed_at, created_at, updated_at
            ) VALUES(1, 'wx-store-11', 'prod-1', 'sku-1', 100,
                     10, '2026-11-18T10:00:00+00:00',
                     '2026-11-18T10:00:00+00:00',
                     '2026-11-18T10:00:00+00:00')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO dongxiao_skus(
                    store_id, store_app_id, product_id, sku_id, price_cents,
                    stock, observed_at, created_at, updated_at
                ) VALUES(1, 'wx-store-11', 'prod-1', ' sku-1 ', 100,
                         10, '2026-11-18T10:00:00+00:00',
                         '2026-11-18T10:00:00+00:00',
                         '2026-11-18T10:00:00+00:00')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO dongxiao_products(
                    store_id, store_app_id, product_id, title, state,
                    min_price_cents, stock_total, observed_at,
                    created_at, updated_at
                ) VALUES(1, 'wx-wrong-app', 'prod-2', '错误店铺', 'active',
                         100, 10, '2026-11-18T10:00:00+00:00',
                         '2026-11-18T10:00:00+00:00',
                         '2026-11-18T10:00:00+00:00')
                """
            )
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
