from __future__ import annotations

import hashlib
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ..domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    ApprovalSubjectType,
    AuditResult,
    DataSource,
    OrderStatus,
    OrderVerificationSource,
    ProductImportStatus,
    ProductPoolStatus,
    ProductState,
    TaskAttemptStatus,
    TaskItemStatus,
    TaskStatus,
)

DONGXIAO_MIGRATION_VERSION = 6
DONGXIAO_MIGRATION_NAME = "dongxiao_release_candidate_v6"
DONGXIAO_V1_CHECKSUM = (
    "6eee2177549390bc74e220626e4f7e395fd7ceb2c069389ab38994f125a3002f"
)
DONGXIAO_V2_CHECKSUM = (
    "9553158b15347f6c19d334091246348105f65e1547f714c7083effd065adc86f"
)
DONGXIAO_V3_CHECKSUM = (
    "21c6a4ab8649fcb24be60d6ab3a2340270c8ed774dc0107c289e9931b9af0984"
)
DONGXIAO_V4_CHECKSUM = (
    "e55a03bd7d8141579f33606961242e4756b242af2aabf7f13e828873038292d5"
)
DONGXIAO_V5_CHECKSUM = (
    "d74be519cddb09bc4afe0f4893e0c91f9cf3a3b303d9c8250fb8a54f6c7eed90"
)
DONGXIAO_V6_CHECKSUM = (
    "26c3bfc3bc3e19fde485fc8972b09e7b630daad303780d2d0ee63ca408d18673"
)


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


def _migration_v2_sql() -> str:
    sources = _values(
        item.value
        for item in (
            DataSource.OFFICIAL_API,
            DataSource.WEB_BACKEND,
            DataSource.LOCAL_CACHE,
            DataSource.MOCK,
            DataSource.UNKNOWN,
        )
    )
    import_statuses = _values(item.value for item in ProductImportStatus)
    statements = [
        """
ALTER TABLE dongxiao_products
    ADD COLUMN sales_count INTEGER
        CHECK (sales_count IS NULL OR sales_count >= 0);
""",
        """
ALTER TABLE dongxiao_products ADD COLUMN sales_source TEXT;
""",
        """
ALTER TABLE dongxiao_products ADD COLUMN listing_time TEXT;
""",
        """
ALTER TABLE dongxiao_products ADD COLUMN listing_source TEXT;
""",
        f"""
ALTER TABLE dongxiao_products
    ADD COLUMN data_source TEXT NOT NULL DEFAULT 'unknown'
        CHECK (data_source IN ({sources}));
""",
        """
ALTER TABLE dongxiao_products ADD COLUMN from_cache INTEGER NOT NULL DEFAULT 0
    CHECK (from_cache IN (0, 1));
""",
        """
ALTER TABLE dongxiao_products ADD COLUMN last_synced_at TEXT;
""",
        """
ALTER TABLE dongxiao_skus ADD COLUMN data_complete INTEGER NOT NULL DEFAULT 1
    CHECK (data_complete IN (0, 1));
""",
        """
ALTER TABLE dongxiao_product_pools
    ADD COLUMN group_name TEXT NOT NULL DEFAULT '';
""",
        """
ALTER TABLE dongxiao_product_pools ADD COLUMN copied_from_pool_id TEXT;
""",
        """
ALTER TABLE dongxiao_product_pools
    ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0);
""",
        """
UPDATE dongxiao_products
SET last_synced_at=observed_at
WHERE last_synced_at IS NULL;
""",
        """
CREATE UNIQUE INDEX IF NOT EXISTS ux_dongxiao_products_normalized_v2
    ON dongxiao_products(store_app_id, trim(product_id));
""",
        """
CREATE UNIQUE INDEX IF NOT EXISTS ux_dongxiao_skus_normalized_v2
    ON dongxiao_skus(
        store_app_id, trim(product_id), trim(COALESCE(sku_id, ''))
    );
""",
        """
CREATE UNIQUE INDEX IF NOT EXISTS ux_dongxiao_pool_items_normalized_v2
    ON dongxiao_product_pool_items(
        pool_id, store_app_id, trim(product_id),
        trim(COALESCE(sku_id, ''))
    );
""",
        """
CREATE UNIQUE INDEX IF NOT EXISTS ux_dongxiao_task_items_normalized_v2
    ON dongxiao_task_items(
        task_id, store_app_id, trim(product_id),
        trim(COALESCE(sku_id, ''))
    );
""",
        f"""
CREATE TABLE IF NOT EXISTS dongxiao_product_imports (
    import_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    product_id TEXT,
    canonical_url TEXT,
    status TEXT NOT NULL CHECK (status IN ({import_statuses})),
    reason TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (store_app_id, dedupe_key)
);
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_imports_store_status
    ON dongxiao_product_imports(store_app_id, status, created_at DESC);
""",
        """
CREATE TABLE IF NOT EXISTS dongxiao_product_pool_events (
    event_id TEXT PRIMARY KEY,
    pool_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    action TEXT NOT NULL,
    product_id TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (pool_id, store_app_id)
        REFERENCES dongxiao_product_pools(pool_id, store_app_id)
        ON DELETE CASCADE
);
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_pool_events_pool
    ON dongxiao_product_pool_events(pool_id, created_at DESC);
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_products_filter_v2
    ON dongxiao_products(
        store_app_id, state, min_price_cents,
        sales_count, listing_time, product_id
    );
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_products_title_v2
    ON dongxiao_products(store_app_id, title);
""",
        """
CREATE TRIGGER IF NOT EXISTS require_dongxiao_sku_business_key_insert
BEFORE INSERT ON dongxiao_skus
WHEN length(trim(COALESCE(NEW.product_id, ''))) = 0
  OR length(trim(COALESCE(NEW.sku_id, ''))) = 0
BEGIN
    SELECT RAISE(ABORT, 'SKU 商品 ID 和 SKU ID 不能为空');
END;
""",
        """
CREATE TRIGGER IF NOT EXISTS require_dongxiao_sku_business_key_update
BEFORE UPDATE OF product_id, sku_id ON dongxiao_skus
WHEN length(trim(COALESCE(NEW.product_id, ''))) = 0
  OR length(trim(COALESCE(NEW.sku_id, ''))) = 0
BEGIN
    SELECT RAISE(ABORT, 'SKU 商品 ID 和 SKU ID 不能为空');
END;
""",
        """
CREATE TRIGGER IF NOT EXISTS require_dongxiao_product_key_insert
BEFORE INSERT ON dongxiao_products
WHEN length(trim(COALESCE(NEW.product_id, ''))) = 0
BEGIN
    SELECT RAISE(ABORT, '商品 ID 不能为空');
END;
""",
    ]
    for table in (
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
    ):
        statements.extend(
            (
                f"""
CREATE TRIGGER IF NOT EXISTS {table}_store_identity_insert_v2
BEFORE INSERT ON {table}
WHEN NEW.store_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM stores
     WHERE id=NEW.store_id AND app_id=NEW.store_app_id
 )
BEGIN
    SELECT RAISE(ABORT, '{table} 店铺 ID 与 AppID 不一致');
END;
""",
                f"""
CREATE TRIGGER IF NOT EXISTS {table}_store_identity_update_v2
BEFORE UPDATE OF store_id, store_app_id ON {table}
WHEN NEW.store_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM stores
     WHERE id=NEW.store_id AND app_id=NEW.store_app_id
 )
BEGIN
    SELECT RAISE(ABORT, '{table} 店铺 ID 与 AppID 不一致');
END;
""",
            )
        )
    return "\n".join(statements)


def _migration_v3_sql() -> str:
    sources = _values(
        item.value
        for item in (
            DataSource.OFFICIAL_API,
            DataSource.WEB_BACKEND,
            DataSource.LOCAL_CACHE,
            DataSource.MOCK,
            DataSource.UNKNOWN,
        )
    )
    return f"""
ALTER TABLE dongxiao_products
    ADD COLUMN presence_state TEXT NOT NULL DEFAULT 'present'
        CHECK (presence_state IN ('present', 'missing'));
ALTER TABLE dongxiao_products ADD COLUMN missing_since TEXT;
ALTER TABLE dongxiao_skus
    ADD COLUMN presence_state TEXT NOT NULL DEFAULT 'present'
        CHECK (presence_state IN ('present', 'missing'));
ALTER TABLE dongxiao_skus ADD COLUMN missing_since TEXT;
ALTER TABLE dongxiao_skus
    ADD COLUMN purchase_min_verified INTEGER NOT NULL DEFAULT 1
        CHECK (purchase_min_verified IN (0, 1));

CREATE INDEX IF NOT EXISTS idx_dongxiao_products_presence_v3
    ON dongxiao_products(store_app_id, presence_state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_dongxiao_skus_presence_v3
    ON dongxiao_skus(
        store_app_id, product_id, presence_state, price_cents, sku_id
    );

CREATE TABLE IF NOT EXISTS dongxiao_catalog_sync_runs (
    run_id TEXT PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ({sources})),
    status TEXT NOT NULL
        CHECK (status IN ('complete', 'partial', 'failed', 'cancelled')),
    discovered INTEGER NOT NULL DEFAULT 0 CHECK (discovered >= 0),
    saved INTEGER NOT NULL DEFAULT 0 CHECK (saved >= 0),
    failed INTEGER NOT NULL DEFAULT 0 CHECK (failed >= 0),
    reconciled_products INTEGER NOT NULL DEFAULT 0
        CHECK (reconciled_products >= 0),
    reconciled_skus INTEGER NOT NULL DEFAULT 0
        CHECK (reconciled_skus >= 0),
    errors_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    CHECK (finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_catalog_sync_runs_latest_v3
    ON dongxiao_catalog_sync_runs(
        store_app_id, finished_at DESC, run_id DESC
    );
CREATE TRIGGER IF NOT EXISTS catalog_sync_runs_store_identity_insert_v3
BEFORE INSERT ON dongxiao_catalog_sync_runs
WHEN NEW.store_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM stores
     WHERE id=NEW.store_id AND app_id=NEW.store_app_id
 )
BEGIN
    SELECT RAISE(ABORT, 'catalog_sync_runs 店铺 ID 与 AppID 不一致');
END;
"""


def _migration_v4_sql() -> str:
    item_states = _values(item.value for item in TaskItemStatus)
    attempt_states = _values(item.value for item in TaskAttemptStatus)
    order_states = _values(item.value for item in OrderStatus)
    order_sources = _values(
        item.value for item in OrderVerificationSource
    )
    statements = [
        """
ALTER TABLE dongxiao_tasks
    ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
""",
        """
ALTER TABLE dongxiao_tasks
    ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3
        CHECK (max_attempts BETWEEN 1 AND 20);
""",
        """
ALTER TABLE dongxiao_tasks ADD COLUMN next_run_at TEXT;
""",
        """
ALTER TABLE dongxiao_tasks ADD COLUMN lease_owner TEXT NOT NULL DEFAULT '';
""",
        """
ALTER TABLE dongxiao_tasks ADD COLUMN lease_expires_at TEXT;
""",
        """
ALTER TABLE dongxiao_tasks ADD COLUMN started_at TEXT;
""",
        """
ALTER TABLE dongxiao_tasks ADD COLUMN finished_at TEXT;
""",
        """
ALTER TABLE dongxiao_tasks
    ADD COLUMN last_error TEXT NOT NULL DEFAULT '';
""",
        """
ALTER TABLE dongxiao_tasks
    ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'local_safe'
        CHECK (execution_mode IN ('local_safe', 'official_read', 'platform_write'));
""",
        f"""
CREATE TABLE IF NOT EXISTS dongxiao_task_execution_items (
    item_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    subject_type TEXT NOT NULL
        CHECK (subject_type IN ('product', 'order', 'aftersale')),
    subject_id TEXT NOT NULL,
    product_id TEXT NOT NULL DEFAULT '',
    sku_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ({item_states})),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3
        CHECK (max_attempts BETWEEN 1 AND 20),
    next_attempt_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    last_error_code TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{{}}',
    result_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (
        task_id, store_app_id, subject_type,
        subject_id, product_id, sku_id
    ),
    UNIQUE (item_id, task_id, store_app_id),
    FOREIGN KEY (task_id, store_app_id)
        REFERENCES dongxiao_tasks(task_id, store_app_id)
        ON DELETE CASCADE
);
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_execution_items_task_v4
    ON dongxiao_task_execution_items(task_id, status, next_attempt_at);
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_execution_items_subject_v4
    ON dongxiao_task_execution_items(
        store_app_id, subject_type, subject_id, updated_at DESC
    );
""",
        f"""
CREATE TABLE IF NOT EXISTS dongxiao_execution_attempts (
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
        REFERENCES dongxiao_task_execution_items(
            item_id, task_id, store_app_id
        )
        ON DELETE CASCADE
);
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_execution_attempts_task_v4
    ON dongxiao_execution_attempts(task_id, started_at DESC);
""",
        f"""
CREATE TABLE IF NOT EXISTS dongxiao_order_details (
    order_ref_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    platform_order_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({order_states})),
    create_time TEXT,
    update_time TEXT,
    pay_time TEXT,
    total_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (total_amount_cents >= 0),
    product_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (product_amount_cents >= 0),
    freight_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (freight_amount_cents >= 0),
    discount_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (discount_amount_cents >= 0),
    item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    items_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL CHECK (source IN ({order_sources})),
    last_synced_at TEXT NOT NULL,
    PRIMARY KEY (order_ref_id, store_app_id),
    UNIQUE (store_app_id, platform_order_id),
    FOREIGN KEY (order_ref_id, store_app_id)
        REFERENCES dongxiao_order_refs(order_ref_id, store_app_id)
        ON DELETE CASCADE
);
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_order_details_latest_v4
    ON dongxiao_order_details(
        store_app_id, update_time DESC, platform_order_id
    );
""",
        """
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN platform_status TEXT NOT NULL DEFAULT '';
""",
        """
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN after_sale_type TEXT NOT NULL DEFAULT '';
""",
        """
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN paid_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (paid_amount_cents >= 0);
""",
        """
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN refunded_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (refunded_amount_cents >= 0);
""",
        """
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN inflight_amount_cents INTEGER NOT NULL DEFAULT 0
        CHECK (inflight_amount_cents >= 0);
""",
        """
ALTER TABLE dongxiao_aftersale_cases ADD COLUMN deadline TEXT;
""",
        """
ALTER TABLE dongxiao_aftersale_cases ADD COLUMN complete_time TEXT;
""",
        """
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}';
""",
        """
ALTER TABLE dongxiao_aftersale_cases ADD COLUMN last_synced_at TEXT;
""",
        """
CREATE INDEX IF NOT EXISTS idx_dongxiao_aftersale_deadline_v4
    ON dongxiao_aftersale_cases(
        store_app_id, status, deadline, updated_at DESC
    );
""",
        """
CREATE TRIGGER IF NOT EXISTS execution_items_store_identity_insert_v4
BEFORE INSERT ON dongxiao_task_execution_items
WHEN NEW.store_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM stores
     WHERE id=NEW.store_id AND app_id=NEW.store_app_id
 )
BEGIN
    SELECT RAISE(ABORT, 'execution_items 店铺 ID 与 AppID 不一致');
END;
""",
        """
CREATE TRIGGER IF NOT EXISTS order_details_store_identity_insert_v4
BEFORE INSERT ON dongxiao_order_details
WHEN NEW.store_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM stores
     WHERE id=NEW.store_id AND app_id=NEW.store_app_id
 )
BEGIN
    SELECT RAISE(ABORT, 'order_details 店铺 ID 与 AppID 不一致');
END;
""",
    ]
    return "\n".join(statements)


def _migration_v5_sql() -> str:
    aftersale_states = _values(item.value for item in AftersaleStatus)
    return f"""
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN platform_state TEXT NOT NULL DEFAULT 'unknown'
        CHECK (platform_state IN ({aftersale_states}));
ALTER TABLE dongxiao_aftersale_cases ADD COLUMN platform_state_updated_at TEXT;
ALTER TABLE dongxiao_aftersale_cases
    ADD COLUMN platform_reason TEXT NOT NULL DEFAULT '';

UPDATE dongxiao_aftersale_cases
SET platform_state=status,
    platform_state_updated_at=COALESCE(last_synced_at, updated_at)
WHERE source='platform_api'
  AND platform_state='unknown';

CREATE TABLE IF NOT EXISTS dongxiao_aftersale_status_history (
    history_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    store_app_id TEXT NOT NULL,
    previous_workflow_status TEXT,
    new_workflow_status TEXT NOT NULL,
    previous_platform_state TEXT,
    new_platform_state TEXT NOT NULL,
    platform_status TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL,
    FOREIGN KEY (case_id, store_app_id)
        REFERENCES dongxiao_aftersale_cases(case_id, store_app_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_aftersale_history_v5
    ON dongxiao_aftersale_status_history(
        store_app_id, case_id, observed_at DESC
    );
INSERT INTO dongxiao_aftersale_status_history(
    history_id, case_id, store_id, store_app_id,
    previous_workflow_status, new_workflow_status,
    previous_platform_state, new_platform_state,
    platform_status, source, actor_id, reason, observed_at
)
SELECT
    lower(hex(randomblob(16))), case_id, store_id, store_app_id,
    NULL, status, NULL, platform_state,
    platform_status, 'migration', 'migration-v5',
    '升级时记录已有状态基线',
    COALESCE(platform_state_updated_at, last_synced_at, updated_at)
FROM dongxiao_aftersale_cases;

UPDATE dongxiao_approvals
SET status='cancelled',
    decided_by='migration-v5',
    decision_reason='迁移发现重复待审批，保留历史并取消较新记录',
    decided_at=COALESCE(decided_at, requested_at)
WHERE subject_type='aftersale'
  AND status='pending'
  AND rowid NOT IN (
      SELECT MIN(rowid)
      FROM dongxiao_approvals
      WHERE subject_type='aftersale' AND status='pending'
      GROUP BY store_app_id, subject_id
  );

CREATE UNIQUE INDEX IF NOT EXISTS ux_dongxiao_approvals_pending_aftersale_v5
    ON dongxiao_approvals(store_app_id, subject_id)
    WHERE subject_type='aftersale' AND status='pending';

CREATE INDEX IF NOT EXISTS idx_dongxiao_tasks_lease_expiry_v5
    ON dongxiao_tasks(status, lease_expires_at, store_app_id);

DROP TRIGGER IF EXISTS prevent_dongxiao_audit_update;
CREATE TRIGGER prevent_dongxiao_audit_update_v5
BEFORE UPDATE ON dongxiao_audit_events
WHEN NOT (
    OLD.store_id IS NOT NULL
    AND NEW.store_id IS NULL
    AND OLD.store_app_id=NEW.store_app_id
    AND OLD.actor_id=NEW.actor_id
    AND OLD.action=NEW.action
    AND OLD.entity_type=NEW.entity_type
    AND OLD.entity_id=NEW.entity_id
    AND OLD.request_id=NEW.request_id
    AND OLD.result=NEW.result
    AND OLD.detail_json=NEW.detail_json
    AND OLD.created_at=NEW.created_at
)
BEGIN
    SELECT RAISE(ABORT, '动销审计事件不可修改');
END;

CREATE TRIGGER IF NOT EXISTS prevent_store_delete_with_active_dongxiao_task_v5
BEFORE DELETE ON stores
WHEN EXISTS (
    SELECT 1 FROM dongxiao_tasks
    WHERE store_id=OLD.id
      AND status IN ('pending', 'preparing', 'running', 'paused', 'unknown')
)
BEGIN
    SELECT RAISE(ABORT, '店铺存在未完成的动销任务，禁止删除');
END;
"""


def _migration_v6_sql() -> str:
    return """
ALTER TABLE dongxiao_tasks ADD COLUMN last_heartbeat_at TEXT;

CREATE INDEX IF NOT EXISTS idx_dongxiao_tasks_heartbeat_v6
    ON dongxiao_tasks(status, last_heartbeat_at, store_app_id);

CREATE TABLE IF NOT EXISTS dongxiao_api_calls (
    call_id TEXT PRIMARY KEY,
    store_app_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    outcome TEXT NOT NULL
        CHECK (outcome IN ('success', 'api_error', 'transport_error')),
    errcode INTEGER,
    rid TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0)
);
CREATE INDEX IF NOT EXISTS idx_dongxiao_api_calls_store_time_v6
    ON dongxiao_api_calls(store_app_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_dongxiao_api_calls_request_v6
    ON dongxiao_api_calls(request_id);

CREATE TABLE IF NOT EXISTS dongxiao_runtime_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO dongxiao_runtime_settings(
    setting_key, setting_value, updated_by, updated_at
) VALUES(
    'platform_writes_enabled', '0', 'migration-v6',
    strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
);
"""


def _migrations() -> tuple[tuple[int, str, Callable[[], str]], ...]:
    return (
        (1, "dongxiao_core_v1", _migration_sql),
        (2, "dongxiao_product_catalog_v2", _migration_v2_sql),
        (3, "dongxiao_catalog_consistency_v3", _migration_v3_sql),
        (4, "dongxiao_operations_v4", _migration_v4_sql),
        (5, "dongxiao_fix1_integrity_v5", _migration_v5_sql),
        (6, "dongxiao_release_candidate_v6", _migration_v6_sql),
    )


def migrate_dongxiao_schema(
    conn: sqlite3.Connection,
    database_path: Path,
    backup_callback: Callable[[str], Path],
) -> bool:
    if conn.in_transaction:
        raise sqlite3.OperationalError(
            "动销中心迁移不能在已打开的事务中执行"
        )

    migrations = _migrations()
    migration_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='dongxiao_schema_migrations'
        """
    ).fetchone()
    applied_versions: dict[int, str] = {}
    if migration_table is not None:
        rows = conn.execute(
            """
            SELECT version, checksum FROM dongxiao_schema_migrations
            ORDER BY version
            """
        ).fetchall()
        applied_versions = {
            int(row[0]): str(row[1])
            for row in rows
        }

    known_versions = {version: (name, loader) for version, name, loader in migrations}
    unknown_versions = sorted(set(applied_versions) - set(known_versions))
    if unknown_versions:
        raise sqlite3.IntegrityError(
            "数据库包含当前源码不认识的动销迁移版本："
            + ", ".join(str(item) for item in unknown_versions)
        )
    for version, checksum in applied_versions.items():
        _name, loader = known_versions[version]
        current_checksum = hashlib.sha256(
            loader().encode("utf-8")
        ).hexdigest()
        if checksum != current_checksum:
            raise sqlite3.IntegrityError(
                f"已应用的动销中心迁移 v{version} 内容被修改"
            )

    pending = [
        (version, name, loader)
        for version, name, loader in migrations
        if version not in applied_versions
    ]
    if not pending:
        _verify_required_tables(conn)
        return False

    _ensure_disk_space(database_path)
    if database_path.exists() and database_path.stat().st_size > 0:
        backup_callback(f"pre-dongxiao-v{pending[-1][0]}")

    for version, name, loader in pending:
        sql = loader()
        current_checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if version == 1 and current_checksum != DONGXIAO_V1_CHECKSUM:
            raise sqlite3.IntegrityError(
                "动销中心 v1 迁移 SQL 已变更，禁止覆盖已发布版本"
            )
        if version == 2 and current_checksum != DONGXIAO_V2_CHECKSUM:
            raise sqlite3.IntegrityError(
                "动销中心 v2 迁移 SQL 已变更，禁止覆盖已发布版本"
            )
        if version == 3 and current_checksum != DONGXIAO_V3_CHECKSUM:
            raise sqlite3.IntegrityError(
                "动销中心 v3 迁移 SQL 已变更，禁止覆盖已发布版本"
            )
        if version == 4 and current_checksum != DONGXIAO_V4_CHECKSUM:
            raise sqlite3.IntegrityError(
                "动销中心 v4 迁移 SQL 已变更，禁止覆盖已发布版本"
            )
        if version == 5 and current_checksum != DONGXIAO_V5_CHECKSUM:
            raise sqlite3.IntegrityError(
                "动销中心 v5 迁移 SQL 已变更，禁止覆盖已发布版本"
            )
        if version == 6 and current_checksum != DONGXIAO_V6_CHECKSUM:
            raise sqlite3.IntegrityError(
                "动销中心 v6 迁移 SQL 已变更，禁止覆盖已发布版本"
            )
        applied_at = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + sql
                + "\nINSERT INTO dongxiao_schema_migrations("
                + "version, name, checksum, applied_at"
                + f") VALUES({version}, '{name}', "
                + f"'{current_checksum}', '{applied_at}');"
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


def _ensure_disk_space(database_path: Path) -> None:
    try:
        usage = shutil.disk_usage(database_path.parent)
    except OSError as exc:
        raise sqlite3.OperationalError(
            "无法确认动销中心迁移所需的磁盘空间"
        ) from exc
    current_size = (
        database_path.stat().st_size
        if database_path.exists()
        else 0
    )
    minimum = 32 * 1024 * 1024
    required = max(minimum, current_size * 2)
    if usage.free < required:
        raise sqlite3.OperationalError(
            f"磁盘可用空间不足，动销迁移至少需要 {required} 字节"
        )


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
        "dongxiao_product_imports",
        "dongxiao_product_pool_events",
        "dongxiao_catalog_sync_runs",
        "dongxiao_task_execution_items",
        "dongxiao_execution_attempts",
        "dongxiao_order_details",
        "dongxiao_aftersale_status_history",
        "dongxiao_api_calls",
        "dongxiao_runtime_settings",
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
