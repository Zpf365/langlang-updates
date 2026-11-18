from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ..domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    ApprovalSubjectType,
    AuditResult,
    OrderStatus,
    OrderVerificationSource,
    ProductPoolStatus,
    ProductState,
    TaskAttemptStatus,
    TaskItemStatus,
    TaskStatus,
)

DONGXIAO_MIGRATION_VERSION = 1
DONGXIAO_MIGRATION_NAME = "dongxiao_core_v1"


def _values(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _migration_sql() -> str:
    product_states = _values(item.value for item in ProductState)
    pool_states = _values(item.value for item in ProductPoolStatus)
    task_states = _values(item.value for item in TaskStatus)
    item_states = _values(item.value for item in TaskItemStatus)
    attempt_states = _values(item.value for item in TaskAttemptStatus)
    order_states = _values(item.value for item in OrderStatus)
    order_sources = _values(item.value for item in OrderVerificationSource)
    aftersale_states = _values(item.value for item in AftersaleStatus)
    approval_states = _values(item.value for item in ApprovalStatus)
    approval_subjects = _values(item.value for item in ApprovalSubjectType)
    audit_results = _values(item.value for item in AuditResult)
    return f"""
CREATE TABLE IF NOT EXISTS dongxiao_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dongxiao_products (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ({product_states})),
    min_price_cents INTEGER NOT NULL
        CHECK (min_price_cents BETWEEN 0 AND 999999999999),
    stock_total INTEGER NOT NULL CHECK (stock_total >= 0),
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (length(trim(store_app_id)) > 0),
    CHECK (length(trim(product_id)) > 0),
    CHECK (length(trim(title)) > 0),
    UNIQUE (store_app_id, product_id),
    UNIQUE (store_app_id, product_id, store_id)
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_products_store_state
    ON dongxiao_products(store_app_id, state, updated_at DESC);

CREATE TABLE IF NOT EXISTS dongxiao_skus (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    specs_json TEXT NOT NULL DEFAULT '[]',
    price_cents INTEGER NOT NULL
        CHECK (price_cents BETWEEN 1 AND 999999999999),
    stock INTEGER NOT NULL CHECK (stock >= 0),
    available INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0, 1)),
    purchase_min INTEGER NOT NULL DEFAULT 1 CHECK (purchase_min > 0),
    purchase_max INTEGER CHECK (purchase_max IS NULL OR purchase_max > 0),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (purchase_max IS NULL OR purchase_max >= purchase_min),
    UNIQUE (store_app_id, product_id, sku_id),
    FOREIGN KEY (store_app_id, product_id)
        REFERENCES dongxiao_products(store_app_id, product_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_skus_product
    ON dongxiao_skus(store_app_id, product_id, available, price_cents);

CREATE TABLE IF NOT EXISTS dongxiao_product_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    price_cents INTEGER NOT NULL
        CHECK (price_cents BETWEEN 0 AND 999999999999),
    stock INTEGER NOT NULL CHECK (stock >= 0),
    available INTEGER NOT NULL DEFAULT 0 CHECK (available IN (0, 1)),
    source TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{{}}',
    observed_at TEXT NOT NULL,
    FOREIGN KEY (store_app_id, product_id)
        REFERENCES dongxiao_products(store_app_id, product_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_product_snapshots_lookup
    ON dongxiao_product_snapshots(
        store_app_id, product_id, sku_id, observed_at DESC
    );

CREATE TABLE IF NOT EXISTS dongxiao_product_pools (
    pool_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({pool_states})),
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (store_app_id, idempotency_key),
    UNIQUE (pool_id, store_app_id)
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_pools_store_status
    ON dongxiao_product_pools(store_app_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS dongxiao_product_pool_items (
    item_id TEXT PRIMARY KEY,
    pool_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku_id TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (pool_id, store_app_id, product_id, sku_id),
    FOREIGN KEY (pool_id, store_app_id)
        REFERENCES dongxiao_product_pools(pool_id, store_app_id)
        ON DELETE CASCADE,
    FOREIGN KEY (store_app_id, product_id)
        REFERENCES dongxiao_products(store_app_id, product_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_pool_items_order
    ON dongxiao_product_pool_items(pool_id, position, item_id);

CREATE TABLE IF NOT EXISTS dongxiao_tasks (
    task_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({task_states})),
    total_items INTEGER NOT NULL DEFAULT 0 CHECK (total_items >= 0),
    succeeded_items INTEGER NOT NULL DEFAULT 0 CHECK (succeeded_items >= 0),
    failed_items INTEGER NOT NULL DEFAULT 0 CHECK (failed_items >= 0),
    status_reason TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (succeeded_items + failed_items <= total_items),
    UNIQUE (store_app_id, idempotency_key),
    UNIQUE (task_id, store_app_id)
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_tasks_store_status
    ON dongxiao_tasks(store_app_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS dongxiao_task_items (
    item_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ({item_states})),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (task_id, store_app_id, product_id, sku_id),
    UNIQUE (item_id, task_id, store_app_id),
    FOREIGN KEY (task_id, store_app_id)
        REFERENCES dongxiao_tasks(task_id, store_app_id)
        ON DELETE CASCADE,
    FOREIGN KEY (store_app_id, product_id)
        REFERENCES dongxiao_products(store_app_id, product_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_task_items_status
    ON dongxiao_task_items(task_id, status, item_id);

CREATE TABLE IF NOT EXISTS dongxiao_task_attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    status TEXT NOT NULL CHECK (status IN ({attempt_states})),
    request_id TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{{}}',
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    UNIQUE (task_id, item_id, attempt_no),
    FOREIGN KEY (item_id, task_id, store_app_id)
        REFERENCES dongxiao_task_items(item_id, task_id, store_app_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_attempts_task
    ON dongxiao_task_attempts(task_id, started_at DESC);

CREATE TABLE IF NOT EXISTS dongxiao_order_refs (
    order_ref_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    platform_order_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({order_states})),
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    source TEXT NOT NULL CHECK (source IN ({order_sources})),
    verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (verified = 0 OR source = 'platform_api'),
    CHECK (
        (verified = 1 AND verified_at IS NOT NULL)
        OR (verified = 0 AND verified_at IS NULL)
    ),
    UNIQUE (store_app_id, platform_order_id),
    UNIQUE (order_ref_id, store_app_id)
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_order_refs_store_status
    ON dongxiao_order_refs(store_app_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS dongxiao_order_status_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    order_ref_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    platform_order_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({order_states})),
    trusted INTEGER NOT NULL DEFAULT 0 CHECK (trusted IN (0, 1)),
    source TEXT NOT NULL CHECK (source IN ({order_sources})),
    request_id TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{{}}',
    observed_at TEXT NOT NULL,
    CHECK (trusted = 0 OR source = 'platform_api'),
    FOREIGN KEY (order_ref_id, store_app_id)
        REFERENCES dongxiao_order_refs(order_ref_id, store_app_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_order_snapshots_lookup
    ON dongxiao_order_status_snapshots(
        store_app_id, platform_order_id, observed_at DESC
    );

CREATE TABLE IF NOT EXISTS dongxiao_aftersale_cases (
    case_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    platform_case_id TEXT NOT NULL,
    order_ref_id TEXT,
    status TEXT NOT NULL CHECK (status IN ({aftersale_states})),
    requested_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (requested_amount_cents BETWEEN 0 AND 999999999999),
    approved_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (approved_amount_cents BETWEEN 0 AND 999999999999),
    idempotency_key TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'unknown',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (approved_amount_cents <= requested_amount_cents),
    UNIQUE (store_app_id, platform_case_id),
    UNIQUE (store_app_id, idempotency_key),
    UNIQUE (case_id, store_app_id),
    FOREIGN KEY (order_ref_id, store_app_id)
        REFERENCES dongxiao_order_refs(order_ref_id, store_app_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_aftersale_store_status
    ON dongxiao_aftersale_cases(store_app_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS dongxiao_approvals (
    approval_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN ({approval_subjects})),
    subject_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({approval_states})),
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided_by TEXT NOT NULL DEFAULT '',
    decision_reason TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    expires_at TEXT,
    CHECK (
        (status IN ('approved', 'rejected')
         AND length(trim(decided_by)) > 0
         AND decided_at IS NOT NULL)
        OR
        (status NOT IN ('approved', 'rejected')
         AND (decided_at IS NULL OR length(trim(decided_by)) > 0))
    )
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_approvals_subject
    ON dongxiao_approvals(
        store_app_id, subject_type, subject_id, requested_at DESC
    );

CREATE TABLE IF NOT EXISTS dongxiao_audit_events (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL DEFAULT '',
    actor_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL CHECK (result IN ({audit_results})),
    detail_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_audit_store_time
    ON dongxiao_audit_events(store_app_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dongxiao_audit_entity
    ON dongxiao_audit_events(entity_type, entity_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS prevent_dongxiao_audit_update
BEFORE UPDATE ON dongxiao_audit_events
BEGIN
    SELECT RAISE(ABORT, '动销审计事件不可修改');
END;

CREATE TRIGGER IF NOT EXISTS prevent_dongxiao_audit_delete
BEFORE DELETE ON dongxiao_audit_events
BEGIN
    SELECT RAISE(ABORT, '动销审计事件不可删除');
END;
"""


def migrate_dongxiao_schema(
    conn: sqlite3.Connection,
    database_path: Path,
    backup_callback: Callable[[str], Path],
) -> bool:
    migration_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='dongxiao_schema_migrations'
        """
    ).fetchone()
    if migration_table is not None:
        applied = conn.execute(
            """
            SELECT checksum FROM dongxiao_schema_migrations
            WHERE version=?
            """,
            (DONGXIAO_MIGRATION_VERSION,),
        ).fetchone()
        if applied is not None:
            current_checksum = hashlib.sha256(
                _migration_sql().encode("utf-8")
            ).hexdigest()
            if str(applied[0]) != current_checksum:
                raise sqlite3.IntegrityError(
                    "已应用的动销中心迁移内容被修改"
                )
            _verify_required_tables(conn)
            return False

    if database_path.exists() and database_path.stat().st_size > 0:
        backup_callback("pre-dongxiao-v1")

    sql = _migration_sql()
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    applied_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + sql
            + "\nINSERT INTO dongxiao_schema_migrations("
            + "version, name, checksum, applied_at"
            + f") VALUES({DONGXIAO_MIGRATION_VERSION}, "
            + f"'{DONGXIAO_MIGRATION_NAME}', '{checksum}', '{applied_at}');"
            + "\nCOMMIT;"
        )
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    _verify_required_tables(conn)
    if database_path.exists() and database_path.stat().st_size <= 0:
        raise sqlite3.IntegrityError("动销中心数据库文件无效")
    return True


def _verify_required_tables(conn: sqlite3.Connection) -> None:
    required = {
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
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = required - tables
    if missing:
        raise sqlite3.IntegrityError(
            "动销中心数据库迁移不完整：" + ", ".join(sorted(missing))
        )
