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
    DONGXIAO_V2_CHECKSUM,
    DONGXIAO_V3_CHECKSUM,
    DONGXIAO_V4_CHECKSUM,
    DONGXIAO_V5_CHECKSUM,
    DONGXIAO_V6_CHECKSUM,
    DONGXIAO_V7_CHECKSUM,
    DONGXIAO_V8_CHECKSUM,
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
    "dongxiao_catalog_sync_runs",
    "dongxiao_task_execution_items",
    "dongxiao_execution_attempts",
    "dongxiao_order_details",
    "dongxiao_aftersale_status_history",
    "dongxiao_api_calls",
    "dongxiao_runtime_settings",
    "dongxiao_store_write_settings",
    "dongxiao_cart_accounts",
    "dongxiao_cart_batches",
    "dongxiao_cart_assignments",
    "dongxiao_cart_results",
    "dongxiao_cart_verifications",
    "dongxiao_cart_mock_state",
    "dongxiao_cart_mock_operations",
    "dongxiao_pending_order_drafts",
    "dongxiao_buyer_accounts",
    "dongxiao_buyer_flow_runs",
    "dongxiao_buyer_flow_items",
    "dongxiao_buyer_order_links",
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
        assert backups == ["pre-dongxiao-v8"]
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)
        ]
        product_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(dongxiao_products)"
            ).fetchall()
        }
        sku_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(dongxiao_skus)"
            ).fetchall()
        }
        assert {"presence_state", "missing_since"} <= product_columns
        assert {
            "presence_state",
            "missing_since",
            "purchase_min_verified",
        } <= sku_columns
        indexes = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA index_list(dongxiao_skus)"
            ).fetchall()
        }
        assert "ux_dongxiao_skus_normalized_v2" in indexes
        assert "idx_dongxiao_skus_presence_v3" in indexes
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

        assert backups == ["pre-dongxiao-v8"]
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
        ).fetchone()[0] == 8
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
        failing_sql = """
            CREATE TABLE dongxiao_failure_probe (id INTEGER);
            INSERT INTO table_that_does_not_exist(id) VALUES(1);
        """
        monkeypatch.setattr(
            migration_module,
            "_migration_v2_sql",
            lambda: failing_sql,
        )
        monkeypatch.setattr(
            migration_module,
            "DONGXIAO_V2_CHECKSUM",
            hashlib.sha256(failing_sql.encode("utf-8")).hexdigest(),
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


def test_v2_checksum_is_frozen_for_published_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_v2_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V2_CHECKSUM


def test_v3_checksum_is_frozen_for_published_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_v3_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V3_CHECKSUM


def test_v4_checksum_is_frozen_for_published_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_v4_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V4_CHECKSUM


def test_v5_checksum_is_frozen_for_published_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_v5_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V5_CHECKSUM


def test_v6_checksum_is_frozen_for_release_candidate_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_v6_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V6_CHECKSUM


def test_v7_checksum_is_frozen_for_final_release_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_v7_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V7_CHECKSUM


def test_v8_checksum_is_frozen_for_real_buyer_flow_migration() -> None:
    assert hashlib.sha256(
        migration_module._migration_v8_sql().encode("utf-8")
    ).hexdigest() == DONGXIAO_V8_CHECKSUM


def test_failed_v3_migration_rolls_back_only_v3_and_keeps_v2_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "rollback-v3.db"
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
        original_v3 = migration_module._migration_v3_sql()
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: (migrations[0], migrations[1]),
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".backup"),
        )
        monkeypatch.undo()
        failing_v3 = """
            CREATE TABLE dongxiao_failure_probe_v3 (id INTEGER);
            INSERT INTO table_that_does_not_exist(id) VALUES(1);
        """
        monkeypatch.setattr(
            migration_module,
            "_migration_v3_sql",
            lambda: failing_v3,
        )
        monkeypatch.setattr(
            migration_module,
            "DONGXIAO_V3_CHECKSUM",
            hashlib.sha256(failing_v3.encode("utf-8")).hexdigest(),
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
        assert "dongxiao_failure_probe_v3" not in tables
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations"
        ).fetchall() == [(1,), (2,)]
        assert conn.execute(
            "SELECT COUNT(*) FROM dongxiao_products"
        ).fetchone()[0] == 0
        assert original_v3
    finally:
        conn.close()


def test_failed_v4_migration_rolls_back_only_v4_and_keeps_v3_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "rollback-v4.db"
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
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:3],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".backup"),
        )
        monkeypatch.undo()
        failing_v4 = """
            CREATE TABLE dongxiao_failure_probe_v4 (id INTEGER);
            INSERT INTO table_that_does_not_exist(id) VALUES(1);
        """
        monkeypatch.setattr(
            migration_module,
            "_migration_v4_sql",
            lambda: failing_v4,
        )
        monkeypatch.setattr(
            migration_module,
            "DONGXIAO_V4_CHECKSUM",
            hashlib.sha256(failing_v4.encode("utf-8")).hexdigest(),
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
        assert "dongxiao_failure_probe_v4" not in tables
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations"
        ).fetchall() == [(1,), (2,), (3,)]
        assert "dongxiao_task_execution_items" not in tables
    finally:
        conn.close()


def test_failed_v5_migration_rolls_back_only_v5_and_keeps_v4_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "rollback-v5.db"
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
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:4],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".backup"),
        )
        monkeypatch.undo()
        failing_v5 = """
            CREATE TABLE dongxiao_failure_probe_v5 (id INTEGER);
            INSERT INTO table_that_does_not_exist(id) VALUES(1);
        """
        monkeypatch.setattr(
            migration_module,
            "_migration_v5_sql",
            lambda: failing_v5,
        )
        monkeypatch.setattr(
            migration_module,
            "DONGXIAO_V5_CHECKSUM",
            hashlib.sha256(failing_v5.encode("utf-8")).hexdigest(),
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
        assert "dongxiao_failure_probe_v5" not in tables
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert "dongxiao_aftersale_status_history" not in tables
    finally:
        conn.close()


def test_v5_upgrades_v4_history_and_deduplicates_pending_approvals(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "upgrade-v5.db"
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
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:4],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".backup"),
        )
        monkeypatch.undo()
        now = "2026-11-18T10:00:00+00:00"
        conn.execute(
            """
            INSERT INTO dongxiao_aftersale_cases(
                case_id, store_id, store_app_id, platform_case_id,
                order_ref_id, status, requested_amount_cents,
                approved_amount_cents, idempotency_key, source, reason,
                created_at, updated_at, platform_status, after_sale_type,
                paid_amount_cents, refunded_amount_cents,
                inflight_amount_cents, details_json, last_synced_at
            ) VALUES(
                'case-v4', 1, 'wx-store-11', 'platform-case-v4',
                NULL, 'approved', 3000, 3000, 'case-v4',
                'platform_api', '历史审批', ?, ?, 'MERCHANT_PROCESSING',
                'REFUND', 10000, 0, 0, '{}', ?
            )
            """,
            (now, now, now),
        )
        for index in (1, 2):
            conn.execute(
                """
                INSERT INTO dongxiao_approvals(
                    approval_id, store_id, store_app_id, subject_type,
                    subject_id, status, requested_by, requested_at,
                    decided_by, decision_reason, decided_at, expires_at
                ) VALUES(
                    ?, 1, 'wx-store-11', 'aftersale', 'case-v4',
                    'pending', 'operator', ?, '', '', NULL, NULL
                )
                """,
                (f"approval-v4-{index}", now),
            )
        conn.commit()

        assert migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v5.backup"),
        )

        row = conn.execute(
            """
            SELECT status, platform_state, platform_state_updated_at
            FROM dongxiao_aftersale_cases
            WHERE case_id='case-v4'
            """
        ).fetchone()
        assert row == ("approved", "approved", now)
        statuses = [
            str(item[0])
            for item in conn.execute(
                """
                SELECT status FROM dongxiao_approvals
                WHERE subject_id='case-v4'
                ORDER BY approval_id
                """
            ).fetchall()
        ]
        assert statuses.count("pending") == 1
        assert statuses.count("cancelled") == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM dongxiao_aftersale_status_history
            WHERE case_id='case-v4'
            """
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)
        ]
    finally:
        conn.close()


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


def test_v6_upgrades_v5_history_and_defaults_platform_writes_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "upgrade-v6.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-v6');
            """
        )
        conn.commit()
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:5],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v5.backup"),
        )
        monkeypatch.undo()
        now = "2026-11-18T10:00:00+00:00"
        conn.execute(
            """
            INSERT INTO dongxiao_tasks(
                task_id, store_id, store_app_id, task_type,
                idempotency_key, request_fingerprint, status,
                created_at, updated_at
            ) VALUES(
                'task-v5', 1, 'wx-store-v6', 'local_validation',
                'idem-v5', 'fingerprint-v5', 'pending', ?, ?
            )
            """,
            (now, now),
        )
        conn.commit()

        assert migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v6.backup"),
        )

        task = conn.execute(
            """
            SELECT last_heartbeat_at
            FROM dongxiao_tasks
            WHERE task_id='task-v5'
            """
        ).fetchone()
        assert task == (None,)
        assert conn.execute(
            """
            SELECT setting_value
            FROM dongxiao_runtime_settings
            WHERE setting_key='platform_writes_enabled'
            """
        ).fetchone() == ("0",)
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)
        ]
        assert not migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".repeat.backup"),
        )
    finally:
        conn.close()


def test_failed_v6_migration_rolls_back_only_v6_and_keeps_v5_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "rollback-v6.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-v6');
            """
        )
        conn.commit()
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:5],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v5.backup"),
        )
        monkeypatch.undo()
        failing_v6 = """
            CREATE TABLE dongxiao_failure_probe_v6 (id INTEGER);
            INSERT INTO table_that_does_not_exist(id) VALUES(1);
        """
        monkeypatch.setattr(
            migration_module,
            "_migration_v6_sql",
            lambda: failing_v6,
        )
        monkeypatch.setattr(
            migration_module,
            "DONGXIAO_V6_CHECKSUM",
            hashlib.sha256(failing_v6.encode("utf-8")).hexdigest(),
        )

        with pytest.raises(sqlite3.OperationalError):
            migrate_dongxiao_schema(
                conn,
                path,
                lambda _label: path.with_suffix(".v6.backup"),
            )

        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "dongxiao_failure_probe_v6" not in tables
        assert "dongxiao_api_calls" not in tables
        assert "dongxiao_runtime_settings" not in tables
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]
    finally:
        conn.close()


def test_v7_upgrades_v6_with_per_store_write_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "upgrade-v7.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-v7');
            """
        )
        conn.commit()
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:6],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v6.backup"),
        )
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:7],
        )
        assert migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v7.backup"),
        )
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='dongxiao_store_write_settings'
            """
        ).fetchone() == ("dongxiao_store_write_settings",)
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,), (7,)
        ]
        assert not migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".repeat.backup"),
        )
    finally:
        conn.close()


def test_failed_v7_migration_rolls_back_only_v7_and_keeps_v6_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "rollback-v7.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-v7');
            """
        )
        conn.commit()
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:6],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v6.backup"),
        )
        monkeypatch.undo()
        failing_v7 = """
            CREATE TABLE dongxiao_failure_probe_v7 (id INTEGER);
            INSERT INTO table_that_does_not_exist(id) VALUES(1);
        """
        monkeypatch.setattr(
            migration_module,
            "_migration_v7_sql",
            lambda: failing_v7,
        )
        monkeypatch.setattr(
            migration_module,
            "DONGXIAO_V7_CHECKSUM",
            hashlib.sha256(failing_v7.encode("utf-8")).hexdigest(),
        )

        with pytest.raises(sqlite3.OperationalError):
            migrate_dongxiao_schema(
                conn,
                path,
                lambda _label: path.with_suffix(".v7.backup"),
            )
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "dongxiao_failure_probe_v7" not in tables
        assert "dongxiao_store_write_settings" not in tables
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,)
        ]
    finally:
        conn.close()


def test_v8_upgrades_v7_with_real_buyer_flow_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "upgrade-v8.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-v8');
            """
        )
        conn.commit()
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:7],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v7.backup"),
        )
        monkeypatch.undo()

        assert migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v8.backup"),
        )
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "dongxiao_buyer_accounts",
            "dongxiao_buyer_flow_runs",
            "dongxiao_buyer_flow_items",
            "dongxiao_buyer_order_links",
        }.issubset(tables)
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)
        ]
        assert not migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".repeat-v8.backup"),
        )
    finally:
        conn.close()


def test_failed_v8_migration_rolls_back_to_complete_v7(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "rollback-v8.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id TEXT NOT NULL UNIQUE
            );
            INSERT INTO stores(app_id) VALUES('wx-store-v8-rollback');
            """
        )
        conn.commit()
        migrations = list(migration_module._migrations())
        monkeypatch.setattr(
            migration_module,
            "_migrations",
            lambda: migrations[:7],
        )
        monkeypatch.setattr(
            migration_module,
            "_verify_required_tables",
            lambda _conn: None,
        )
        migrate_dongxiao_schema(
            conn,
            path,
            lambda _label: path.with_suffix(".v7.backup"),
        )
        monkeypatch.undo()
        failing_v8 = """
            CREATE TABLE dongxiao_failure_probe_v8 (id INTEGER);
            INSERT INTO table_that_does_not_exist(id) VALUES(1);
        """
        monkeypatch.setattr(
            migration_module,
            "_migration_v8_sql",
            lambda: failing_v8,
        )
        monkeypatch.setattr(
            migration_module,
            "DONGXIAO_V8_CHECKSUM",
            hashlib.sha256(failing_v8.encode("utf-8")).hexdigest(),
        )

        with pytest.raises(sqlite3.OperationalError):
            migrate_dongxiao_schema(
                conn,
                path,
                lambda _label: path.with_suffix(".v8-failed.backup"),
            )

        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "dongxiao_failure_probe_v8" not in tables
        assert "dongxiao_buyer_accounts" not in tables
        assert conn.execute(
            "SELECT version FROM dongxiao_schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,), (2,), (3,), (4,), (5,), (6,), (7,)
        ]
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
