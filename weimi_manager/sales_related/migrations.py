from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .constants import REFUND_STATUSES, TASK_STATUSES

SALES_RELATED_MIGRATION_VERSION = 1
SALES_RELATED_MIGRATION_SCOPE = "sales_related"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _migration_sql() -> str:
    task_statuses = _quoted(TASK_STATUSES)
    refund_statuses = _quoted(REFUND_STATUSES)
    return f"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    scope TEXT NOT NULL,
    version INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (scope, version)
);

CREATE TABLE IF NOT EXISTS sales_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    submission_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    store_id INTEGER,
    store_app_id TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    buyer_ref TEXT NOT NULL DEFAULT '',
    sku_selection_mode TEXT NOT NULL,
    specified_sku_id TEXT NOT NULL DEFAULT '',
    specified_specs_json TEXT NOT NULL DEFAULT '{{}}',
    purchase_quantity INTEGER NOT NULL CHECK (purchase_quantity > 0),
    price_cap_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cap_cents >= 0),
    status TEXT NOT NULL CHECK (status IN ({task_statuses})),
    status_reason TEXT NOT NULL DEFAULT '',
    simulation_mode INTEGER NOT NULL DEFAULT 1 CHECK (simulation_mode IN (0, 1)),
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_tasks_submission
    ON sales_tasks(submission_id)
    WHERE submission_id <> '';
CREATE INDEX IF NOT EXISTS idx_sales_tasks_status_created
    ON sales_tasks(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_tasks_store
    ON sales_tasks(store_app_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_tasks_device
    ON sales_tasks(device_id, status);

CREATE TABLE IF NOT EXISTS sales_task_sku_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    product_id TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL,
    specs_json TEXT NOT NULL DEFAULT '{{}}',
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    stock INTEGER NOT NULL DEFAULT 0,
    limit_min INTEGER NOT NULL DEFAULT 1,
    limit_max INTEGER,
    available INTEGER NOT NULL DEFAULT 0,
    complete INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES sales_tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sales_task_skus_task
    ON sales_task_sku_snapshots(task_id, sku_id);

CREATE TABLE IF NOT EXISTS sales_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    submission_id TEXT NOT NULL UNIQUE,
    store_id INTEGER,
    store_app_id TEXT NOT NULL DEFAULT '',
    order_id TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    product_amount_cents INTEGER NOT NULL DEFAULT 0,
    freight_cents INTEGER NOT NULL DEFAULT 0,
    discount_cents INTEGER NOT NULL DEFAULT 0,
    payable_cents INTEGER NOT NULL DEFAULT 0,
    order_status TEXT NOT NULL DEFAULT '',
    payment_status TEXT NOT NULL DEFAULT '',
    verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES sales_tasks(task_id) ON DELETE RESTRICT,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_orders_store_order
    ON sales_orders(store_app_id, order_id)
    WHERE store_app_id <> '' AND order_id <> '';

CREATE TABLE IF NOT EXISTS sales_order_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    source TEXT NOT NULL,
    outcome TEXT NOT NULL,
    trusted INTEGER NOT NULL DEFAULT 0,
    order_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{{}}',
    observed_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES sales_tasks(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sales_verifications_task
    ON sales_order_verifications(task_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS sales_devices (
    device_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offline',
    capabilities_json TEXT NOT NULL DEFAULT '{{}}',
    app_version TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_device_leases (
    task_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    lease_token_hash TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    FOREIGN KEY (task_id) REFERENCES sales_tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES sales_devices(device_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_sales_leases_device
    ON sales_device_leases(device_id, status, lease_expires_at);

CREATE TABLE IF NOT EXISTS sales_buyer_whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    store_app_id TEXT NOT NULL,
    buyer_identifier_type TEXT NOT NULL,
    buyer_identifier TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    allowed_after_sale_types_json TEXT NOT NULL DEFAULT '[]',
    single_refund_limit_cents INTEGER NOT NULL DEFAULT 0,
    daily_refund_limit_cents INTEGER NOT NULL DEFAULT 0,
    monthly_refund_limit_cents INTEGER NOT NULL DEFAULT 0,
    allowed_order_ids_json TEXT NOT NULL DEFAULT '[]',
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    emergency_stop INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(store_app_id, buyer_identifier_type, buyer_identifier),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sales_whitelist_lookup
    ON sales_buyer_whitelist(
        store_app_id, buyer_identifier_type, buyer_identifier, enabled
    );

CREATE TABLE IF NOT EXISTS sales_aftersale_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    order_id TEXT NOT NULL,
    store_app_id TEXT NOT NULL,
    buyer_identifier_type TEXT NOT NULL,
    buyer_identifier TEXT NOT NULL,
    after_sale_type TEXT NOT NULL,
    requested_amount_cents INTEGER NOT NULL DEFAULT 0,
    approved_amount_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ({refund_statuses})),
    decision TEXT NOT NULL DEFAULT '',
    decision_reason TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL UNIQUE,
    simulation_mode INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES sales_tasks(task_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_aftersale_order
    ON sales_aftersale_cases(store_app_id, order_id, created_at DESC);

CREATE TABLE IF NOT EXISTS sales_audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_audit_task
    ON sales_audit_logs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sales_audit_device
    ON sales_audit_logs(device_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS prevent_sales_audit_update
BEFORE UPDATE ON sales_audit_logs
BEGIN
    SELECT RAISE(ABORT, '动销相关审计日志不可修改');
END;

CREATE TRIGGER IF NOT EXISTS prevent_sales_audit_delete
BEFORE DELETE ON sales_audit_logs
BEGIN
    SELECT RAISE(ABORT, '动销相关审计日志不可删除');
END;
"""


def migrate_sales_related_schema(
    conn: sqlite3.Connection,
    database_path: Path,
    backup_callback: Callable[[str], Path],
) -> bool:
    """Apply the independent sales-related schema once and return changed state."""
    migration_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='schema_migrations'
        """
    ).fetchone()
    if migration_table is not None:
        applied = conn.execute(
            """
            SELECT 1 FROM schema_migrations
            WHERE scope=? AND version=?
            """,
            (SALES_RELATED_MIGRATION_SCOPE, SALES_RELATED_MIGRATION_VERSION),
        ).fetchone()
        if applied is not None:
            return False

    existing_data = False
    store_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='stores'
        """
    ).fetchone()
    if store_table is not None:
        existing_data = bool(
            conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
        )
    if existing_data:
        backup_callback("pre-sales-related-v1")

    sql = _migration_sql()
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + sql
            + "\nINSERT INTO schema_migrations(scope, version, checksum, applied_at)"
            + f" VALUES('{SALES_RELATED_MIGRATION_SCOPE}', "
            + f"{SALES_RELATED_MIGRATION_VERSION}, '{checksum}', '{now}');"
            + "\nCOMMIT;"
        )
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise

    tables = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name LIKE 'sales_%'
            """
        ).fetchall()
    }
    required = {
        "sales_tasks",
        "sales_task_sku_snapshots",
        "sales_orders",
        "sales_order_verifications",
        "sales_devices",
        "sales_device_leases",
        "sales_buyer_whitelist",
        "sales_aftersale_cases",
        "sales_audit_logs",
    }
    if not required.issubset(tables):
        raise sqlite3.IntegrityError("动销相关数据库迁移不完整")
    if database_path.exists() and database_path.stat().st_size <= 0:
        raise sqlite3.IntegrityError("动销相关数据库文件无效")
    return True
