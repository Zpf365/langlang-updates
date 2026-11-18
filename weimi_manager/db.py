from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

from .dongxiao.infrastructure.migrations import migrate_dongxiao_schema
from .models import (
    ORDER_STATUS,
    PRODUCT_STATUS,
    Order,
    OrderItem,
    Product,
    Store,
    product_matches_tab,
)
from .sales_related.migrations import migrate_sales_related_schema
from .security import CredentialProtector

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    app_id TEXT NOT NULL UNIQUE,
    secret_protected BLOB NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '未验证',
    web_status TEXT NOT NULL DEFAULT '未登录',
    web_status_detail TEXT NOT NULL DEFAULT '',
    web_checked_at TEXT,
    web_logged_in_at TEXT,
    web_session_updated_at TEXT NOT NULL DEFAULT '',
    web_cookies_protected BLOB,
    last_sync_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stores_group_name
    ON stores(group_name, name);
CREATE INDEX IF NOT EXISTS idx_stores_web_status
    ON stores(web_status);

CREATE TABLE IF NOT EXISTS store_groups (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    store_id INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    status INTEGER NOT NULL,
    status_text TEXT NOT NULL,
    create_time INTEGER NOT NULL,
    update_time INTEGER NOT NULL,
    pay_time INTEGER NOT NULL DEFAULT 0,
    total_amount INTEGER NOT NULL DEFAULT 0,
    product_amount INTEGER NOT NULL DEFAULT 0,
    freight_amount INTEGER NOT NULL DEFAULT 0,
    discount_amount INTEGER NOT NULL DEFAULT 0,
    buyer_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    waybill_id TEXT NOT NULL DEFAULT '',
    carrier TEXT NOT NULL DEFAULT '',
    merchant_notes TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (store_id, order_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(create_time DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_store_created
    ON orders(store_id, create_time DESC, status);

CREATE TABLE IF NOT EXISTS order_workbench (
    store_id INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    purchase_order_no TEXT NOT NULL DEFAULT '',
    pickup_code TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    profit INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL DEFAULT 0,
    is_marked INTEGER NOT NULL DEFAULT 0,
    is_marked_sent INTEGER NOT NULL DEFAULT 0,
    parsed_at TEXT NOT NULL DEFAULT '',
    fulfillment_stage TEXT NOT NULL DEFAULT '',
    purchased_at TEXT NOT NULL DEFAULT '',
    shipped_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    flag_color TEXT NOT NULL DEFAULT '',
    ignored INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (store_id, order_id),
    FOREIGN KEY (store_id, order_id)
        REFERENCES orders(store_id, order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS procurement_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    label TEXT NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    login_id TEXT NOT NULL DEFAULT '',
    profile_dir TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '未登录',
    auto_login INTEGER NOT NULL DEFAULT 0,
    account_type INTEGER NOT NULL DEFAULT 2,
    alipay_uid TEXT NOT NULL DEFAULT '',
    last_checked_at TEXT,
    last_login TEXT,
    last_sync TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procurement_account_secrets (
    account_id INTEGER PRIMARY KEY,
    password_protected BLOB,
    alipay_password_protected BLOB,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (account_id)
        REFERENCES procurement_accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS order_dispatch_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL DEFAULT '',
    store_sku_id TEXT NOT NULL DEFAULT '',
    purchase_link_id INTEGER,
    source_url TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(store_id, order_id, product_id, store_sku_id),
    FOREIGN KEY (store_id, order_id)
        REFERENCES orders(store_id, order_id) ON DELETE CASCADE,
    FOREIGN KEY (purchase_link_id)
        REFERENCES product_purchase_links(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_order_dispatch_status
    ON order_dispatch_tasks(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS procurement_sessions (
    account_id INTEGER PRIMARY KEY,
    payload_protected BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES procurement_accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS procurement_alipay_sessions (
    account_id INTEGER PRIMARY KEY,
    payload_protected BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES procurement_accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS store_procurement_accounts (
    store_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    PRIMARY KEY (store_id, account_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES procurement_accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS procurement_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    external_order_id TEXT NOT NULL DEFAULT '',
    trade_no TEXT NOT NULL DEFAULT '',
    mail_no TEXT NOT NULL DEFAULT '',
    pickup_code TEXT NOT NULL DEFAULT '',
    station_name TEXT NOT NULL DEFAULT '',
    station_phone TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    status_text TEXT NOT NULL DEFAULT '',
    goods_title TEXT NOT NULL DEFAULT '',
    goods_image TEXT NOT NULL DEFAULT '',
    goods_count INTEGER NOT NULL DEFAULT 0,
    pay_amount INTEGER NOT NULL DEFAULT 0,
    seller TEXT NOT NULL DEFAULT '',
    buyer TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    fetched_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_id, external_order_id)
);

CREATE TABLE IF NOT EXISTS procurement_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    procurement_order_id INTEGER NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'manual',
    confidence INTEGER NOT NULL DEFAULT 100,
    local_note TEXT NOT NULL DEFAULT '',
    sync_wechat_note INTEGER NOT NULL DEFAULT 0,
    wechat_note_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(store_id, order_id, procurement_order_id),
    FOREIGN KEY (store_id, order_id)
        REFERENCES orders(store_id, order_id) ON DELETE CASCADE,
    FOREIGN KEY (procurement_order_id)
        REFERENCES procurement_orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hosted_configs (
    store_id INTEGER PRIMARY KEY,
    revoke_audit INTEGER NOT NULL DEFAULT 0,
    delete_audit_failed INTEGER NOT NULL DEFAULT 0,
    list_products INTEGER NOT NULL DEFAULT 0,
    prefill_11 INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hosted_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    actions TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS automation_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    store_id INTEGER,
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    next_run TEXT,
    last_status TEXT NOT NULL DEFAULT '',
    last_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES automation_tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS order_items (
    store_id INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    sku_count INTEGER NOT NULL DEFAULT 0,
    sale_price INTEGER NOT NULL DEFAULT 0,
    thumb_url TEXT NOT NULL DEFAULT '',
    sku_attributes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (store_id, order_id, product_id, sku_id),
    FOREIGN KEY (store_id, order_id)
        REFERENCES orders(store_id, order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS products (
    store_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL DEFAULT 0,
    status_text TEXT NOT NULL DEFAULT '',
    edit_status INTEGER NOT NULL DEFAULT 0,
    min_price INTEGER NOT NULL DEFAULT 0,
    sku_count INTEGER NOT NULL DEFAULT 0,
    stock_total INTEGER NOT NULL DEFAULT 0,
    spu_code TEXT NOT NULL DEFAULT '',
    out_product_id TEXT NOT NULL DEFAULT '',
    head_img TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (store_id, product_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);

CREATE TABLE IF NOT EXISTS product_catalog (
    store_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    detail_synced_at TEXT,
    last_scan_id TEXT NOT NULL,
    PRIMARY KEY (store_id, product_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_catalog_state
    ON product_catalog(store_id, state);
CREATE INDEX IF NOT EXISTS idx_products_store_status
    ON products(store_id, status);

CREATE TABLE IF NOT EXISTS product_status_snapshot (
    store_id INTEGER NOT NULL,
    tab_key TEXT NOT NULL,
    product_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (store_id, tab_key),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_metrics (
    store_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    views30 INTEGER NOT NULL DEFAULT 0,
    sold30 INTEGER NOT NULL DEFAULT 0,
    click_cnt INTEGER NOT NULL DEFAULT 0,
    click_uv INTEGER NOT NULL DEFAULT 0,
    pay_cnt INTEGER NOT NULL DEFAULT 0,
    pay_uv INTEGER NOT NULL DEFAULT 0,
    pay_gmv INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (store_id, product_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_scan_state (
    store_id INTEGER PRIMARY KEY,
    scan_id TEXT NOT NULL,
    next_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    discovered INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipping_config (
    store_id INTEGER PRIMARY KEY,
    sender_name TEXT NOT NULL DEFAULT '',
    sender_mobile TEXT NOT NULL DEFAULT '',
    sender_province TEXT NOT NULL DEFAULT '',
    sender_city TEXT NOT NULL DEFAULT '',
    sender_county TEXT NOT NULL DEFAULT '',
    sender_street TEXT NOT NULL DEFAULT '',
    sender_address TEXT NOT NULL DEFAULT '',
    default_acct_id TEXT NOT NULL DEFAULT '',
    default_delivery_id TEXT NOT NULL DEFAULT '',
    default_template_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipping_accounts (
    store_id INTEGER NOT NULL,
    acct_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL DEFAULT '',
    delivery_name TEXT NOT NULL DEFAULT '',
    shop_id TEXT NOT NULL DEFAULT '',
    site_code TEXT NOT NULL DEFAULT '',
    site_name TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 0,
    monthly_card TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    sender_mobile TEXT NOT NULL DEFAULT '',
    sender_address_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (store_id, acct_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipping_templates (
    store_id INTEGER NOT NULL,
    delivery_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_name TEXT NOT NULL DEFAULT '',
    template_type TEXT NOT NULL DEFAULT 'single',
    is_default INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (store_id, delivery_id, template_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipments (
    store_id INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    ewaybill_order_id TEXT NOT NULL DEFAULT '',
    delivery_id TEXT NOT NULL DEFAULT '',
    acct_id TEXT NOT NULL DEFAULT '',
    template_id TEXT NOT NULL DEFAULT '',
    waybill_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    print_info TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (store_id, order_id),
    FOREIGN KEY (store_id, order_id)
        REFERENCES orders(store_id, order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS store_profiles (
    store_id INTEGER PRIMARY KEY,
    nickname TEXT NOT NULL DEFAULT '',
    headimg_url TEXT NOT NULL DEFAULT '',
    subject_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    is_local_life INTEGER NOT NULL DEFAULT 0,
    open_timestamp INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS after_sales (
    store_id INTEGER NOT NULL,
    after_sale_order_id TEXT NOT NULL,
    order_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    status_text TEXT NOT NULL DEFAULT '',
    after_sale_type TEXT NOT NULL DEFAULT '',
    reason_text TEXT NOT NULL DEFAULT '',
    refund_amount INTEGER NOT NULL DEFAULT 0,
    create_time INTEGER NOT NULL DEFAULT 0,
    update_time INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (store_id, after_sale_order_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_after_sales_status
    ON after_sales(store_id, status, update_time DESC);

CREATE TABLE IF NOT EXISTS transfer_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_type TEXT NOT NULL DEFAULT 'internal',
    store_id INTEGER,
    order_id TEXT NOT NULL DEFAULT '',
    procurement_order_id INTEGER,
    source_order_no TEXT NOT NULL DEFAULT '',
    transfer_order_no TEXT NOT NULL DEFAULT '',
    work_order_no TEXT NOT NULL DEFAULT '',
    tracking_no TEXT NOT NULL DEFAULT '',
    pickup_code TEXT NOT NULL DEFAULT '',
    warehouse TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '待处理',
    status_text TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    source_created_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(case_type, transfer_order_no)
);

CREATE INDEX IF NOT EXISTS idx_transfer_cases_type_status
    ON transfer_cases(case_type, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_transfer_cases_order
    ON transfer_cases(order_id, procurement_order_id);

CREATE TABLE IF NOT EXISTS product_purchase_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    use_transfer_warehouse INTEGER NOT NULL DEFAULT 0,
    commission_pct REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    address_info_json TEXT NOT NULL DEFAULT '{}',
    source_product_id TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    source_skus_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_purchase_links_product
    ON product_purchase_links(store_id, product_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS product_sku_mappings (
    store_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    store_sku_id TEXT NOT NULL,
    purchase_link_id INTEGER NOT NULL,
    purchase_sku_id TEXT NOT NULL DEFAULT '',
    purchase_attrs_json TEXT NOT NULL DEFAULT '[]',
    purchase_price INTEGER NOT NULL DEFAULT 0,
    purchase_image TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (store_id, product_id, store_sku_id),
    FOREIGN KEY (purchase_link_id)
        REFERENCES product_purchase_links(id) ON DELETE CASCADE,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_operation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    store_id INTEGER,
    store_name TEXT NOT NULL DEFAULT '',
    activity_id INTEGER,
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_operation_runs_created
    ON activity_operation_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS analytics_daily (
    store_id INTEGER NOT NULL,
    business_date TEXT NOT NULL,
    pay_gmv INTEGER NOT NULL DEFAULT 0,
    pay_uv INTEGER NOT NULL DEFAULT 0,
    pay_order_cnt INTEGER NOT NULL DEFAULT 0,
    pay_refund_gmv INTEGER NOT NULL DEFAULT 0,
    live_pay_gmv INTEGER NOT NULL DEFAULT 0,
    feed_pay_gmv INTEGER NOT NULL DEFAULT 0,
    product_click_uv INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (store_id, business_date),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS coupons (
    store_id INTEGER NOT NULL,
    coupon_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    coupon_type INTEGER NOT NULL DEFAULT 0,
    status INTEGER NOT NULL DEFAULT 0,
    discount_fee INTEGER NOT NULL DEFAULT 0,
    discount_num INTEGER NOT NULL DEFAULT 0,
    total_num INTEGER NOT NULL DEFAULT 0,
    receive_num INTEGER NOT NULL DEFAULT 0,
    used_num INTEGER NOT NULL DEFAULT 0,
    start_time INTEGER NOT NULL DEFAULT 0,
    end_time INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (store_id, coupon_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    message TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    store_id INTEGER,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_task_logs_started
    ON task_logs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_logs_scope
    ON task_logs(scope, started_at DESC);

CREATE TABLE IF NOT EXISTS upload_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    source_product_id TEXT NOT NULL,
    product_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL,
    UNIQUE(store_id, source_product_id),
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_upload_history_source
    ON upload_history(source_product_id);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER,
    resource TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE SET NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _incoming_session_is_not_older(
    incoming: str,
    current: str,
) -> bool:
    if not incoming:
        return False
    if not current:
        return True
    try:
        incoming_time = datetime.fromisoformat(
            incoming.replace("Z", "+00:00")
        )
        current_time = datetime.fromisoformat(
            current.replace("Z", "+00:00")
        )
    except ValueError:
        return incoming >= current
    return incoming_time >= current_time


class Database:
    def __init__(self, path: Path, protector: CredentialProtector | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protector = protector or CredentialProtector()
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        if self.path.exists() and self.path.stat().st_size:
            probe = sqlite3.connect(self.path, timeout=15)
            try:
                existing_tables = {
                    str(row[0])
                    for row in probe.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                workbench_columns = (
                    {
                        str(row[1])
                        for row in probe.execute(
                            "PRAGMA table_info(order_workbench)"
                        ).fetchall()
                    }
                    if "order_workbench" in existing_tables
                    else set()
                )
                procurement_columns = (
                    {
                        str(row[1])
                        for row in probe.execute(
                            "PRAGMA table_info(procurement_accounts)"
                        ).fetchall()
                    }
                    if "procurement_accounts" in existing_tables
                    else set()
                )
            finally:
                probe.close()
            if existing_tables and "transfer_cases" not in existing_tables:
                self._create_migration_backup("pre-transfer-cases")
            elif existing_tables and (
                "product_purchase_links" not in existing_tables
                or "product_sku_mappings" not in existing_tables
            ):
                self._create_migration_backup("pre-product-sku-links")
            elif existing_tables and (
                "order_dispatch_tasks" not in existing_tables
                or (
                    workbench_columns
                    and not {
                        "fulfillment_stage",
                        "purchased_at",
                        "shipped_at",
                        "completed_at",
                        "last_error",
                        "flag_color",
                        "ignored",
                    }.issubset(workbench_columns)
                )
            ):
                self._create_migration_backup("pre-order-workflow")
            if existing_tables and "procurement_accounts" in existing_tables and (
                not {
                    "nickname",
                    "group_name",
                    "remark",
                    "auto_login",
                    "account_type",
                    "alipay_uid",
                    "last_checked_at",
                }.issubset(procurement_columns)
            ):
                self._create_migration_backup("pre-procurement-account-fields")
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_procurement_link_scope(conn)
            existing_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(stores)").fetchall()
            }
            migrations = {
                "web_status": "TEXT NOT NULL DEFAULT '未登录'",
                "web_status_detail": "TEXT NOT NULL DEFAULT ''",
                "web_checked_at": "TEXT",
                "web_logged_in_at": "TEXT",
                "web_session_updated_at": "TEXT NOT NULL DEFAULT ''",
                "web_cookies_protected": "BLOB",
            }
            for column, definition in migrations.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE stores ADD COLUMN {column} {definition}")
            procurement_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(procurement_accounts)"
                ).fetchall()
            }
            procurement_migrations = {
                "nickname": "TEXT NOT NULL DEFAULT ''",
                "group_name": "TEXT NOT NULL DEFAULT ''",
                "remark": "TEXT NOT NULL DEFAULT ''",
                "auto_login": "INTEGER NOT NULL DEFAULT 0",
                "account_type": "INTEGER NOT NULL DEFAULT 2",
                "alipay_uid": "TEXT NOT NULL DEFAULT ''",
                "last_checked_at": "TEXT",
            }
            for column, definition in procurement_migrations.items():
                if column not in procurement_columns:
                    conn.execute(
                        "ALTER TABLE procurement_accounts "
                        f"ADD COLUMN {column} {definition}"
                    )
            now = _now()
            conn.execute(
                """
                UPDATE stores
                SET web_session_updated_at=COALESCE(
                    NULLIF(web_session_updated_at, ''),
                    NULLIF(web_checked_at, ''),
                    NULLIF(updated_at, ''),
                    ?
                )
                WHERE TRIM(COALESCE(web_session_updated_at, ''))=''
                """,
                (now,),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO store_groups (name, created_at, updated_at)
                SELECT DISTINCT TRIM(group_name), ?, ?
                FROM stores
                WHERE TRIM(group_name) <> ''
                """,
                (now, now),
            )
            workbench_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(order_workbench)").fetchall()
            }
            if "parsed_at" not in workbench_columns:
                conn.execute(
                    "ALTER TABLE order_workbench "
                    "ADD COLUMN parsed_at TEXT NOT NULL DEFAULT ''"
                )
            for column in (
                "fulfillment_stage",
                "purchased_at",
                "shipped_at",
                "completed_at",
                "last_error",
                "flag_color",
                "ignored",
            ):
                if column not in workbench_columns:
                    conn.execute(
                        "ALTER TABLE order_workbench "
                        f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            conn.execute(
                """
                INSERT OR IGNORE INTO product_catalog (
                    store_id, product_id, state, attempts, last_error,
                    first_seen_at, last_seen_at, detail_synced_at, last_scan_id
                )
                SELECT store_id, product_id, 'done', 0, '', ?, ?, ?, 'migration'
                FROM products
                """,
                (now, now, now),
            )
            conn.commit()
            migrate_sales_related_schema(
                conn,
                self.path,
                self._create_migration_backup,
            )
            migrate_dongxiao_schema(
                conn,
                self.path,
                self._create_migration_backup,
            )

    @staticmethod
    def _procurement_link_scope_needs_migration(
        conn: sqlite3.Connection,
    ) -> bool:
        indexes = conn.execute(
            "PRAGMA index_list(procurement_links)"
        ).fetchall()
        for index in indexes:
            if not int(index["unique"]):
                continue
            columns = [
                str(row["name"])
                for row in conn.execute(
                    f"PRAGMA index_info({index['name']})"
                ).fetchall()
            ]
            if columns == ["order_id", "procurement_order_id"]:
                return True
        return False

    def _create_migration_backup(self, label: str) -> Path:
        base = self.path.with_name(f"{self.path.name}.{label}.bak")
        destination = base
        suffix = 2
        while destination.exists():
            destination = base.with_name(f"{base.name}.{suffix}")
            suffix += 1
        destination_conn = sqlite3.connect(destination, timeout=15)
        source_conn = sqlite3.connect(self.path, timeout=15)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
            source_conn.close()
        return destination

    def _migrate_procurement_link_scope(
        self,
        conn: sqlite3.Connection,
    ) -> None:
        if not self._procurement_link_scope_needs_migration(conn):
            return
        self._create_migration_backup("pre-ll003")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "ALTER TABLE procurement_links RENAME TO procurement_links_ll003_old"
        )
        conn.execute(
            """
            CREATE TABLE procurement_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                procurement_order_id INTEGER NOT NULL,
                match_type TEXT NOT NULL DEFAULT 'manual',
                confidence INTEGER NOT NULL DEFAULT 100,
                local_note TEXT NOT NULL DEFAULT '',
                sync_wechat_note INTEGER NOT NULL DEFAULT 0,
                wechat_note_synced_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(store_id, order_id, procurement_order_id),
                FOREIGN KEY (store_id, order_id)
                    REFERENCES orders(store_id, order_id) ON DELETE CASCADE,
                FOREIGN KEY (procurement_order_id)
                    REFERENCES procurement_orders(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO procurement_links (
                id, store_id, order_id, procurement_order_id, match_type,
                confidence, local_note, sync_wechat_note, wechat_note_synced_at,
                created_at, updated_at
            )
            SELECT
                id, store_id, order_id, procurement_order_id, match_type,
                confidence, local_note, sync_wechat_note, wechat_note_synced_at,
                created_at, updated_at
            FROM procurement_links_ll003_old
            """
        )
        old_count = conn.execute(
            "SELECT COUNT(*) FROM procurement_links_ll003_old"
        ).fetchone()[0]
        new_count = conn.execute(
            "SELECT COUNT(*) FROM procurement_links"
        ).fetchone()[0]
        if old_count != new_count:
            raise sqlite3.IntegrityError(
                "采购关联迁移行数不一致，已回滚"
            )
        conn.execute("DROP TABLE procurement_links_ll003_old")

    def backup_to(self, destination: Path) -> None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(self.path, timeout=15)
        destination_conn = sqlite3.connect(destination, timeout=15)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
            source_conn.close()

    def restore_from(self, source: Path) -> None:
        source = Path(source)
        if not source.exists():
            raise ValueError("备份文件不存在")
        source_conn = sqlite3.connect(source, timeout=15)
        destination_conn = sqlite3.connect(self.path, timeout=15)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
            source_conn.close()

    def auto_backup(
        self,
        backup_dir: Path,
        keep: int = 30,
        minimum_interval_hours: int = 4,
    ) -> Path | None:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        try:
            last_backup = float(
                self.get_setting("automatic_backup_at", "") or 0
            )
        except (TypeError, ValueError):
            last_backup = 0
        if (
            last_backup > 0
            and now.timestamp() - last_backup
            < max(1, int(minimum_interval_hours)) * 3600
        ):
            return None
        destination = backup_dir / (
            f"weimi_manager_{datetime.now():%Y%m%d_%H%M%S}.db"
        )
        self.backup_to(destination)
        backups = sorted(
            backup_dir.glob("weimi_manager_*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[max(1, int(keep)) :]:
            try:
                old_backup.unlink()
            except OSError:
                continue
        self.set_setting(
            "automatic_backup_at",
            f"{now.timestamp():.0f}",
        )
        self.set_setting(
            "automatic_backup_date",
            now.date().isoformat(),
        )
        return destination

    def store_summary_map(self) -> dict[int, dict[str, int]]:
        now = datetime.now()
        today_start = int(
            now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp()
        )
        week_start = int(
            (now - timedelta(days=6))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        month_start = int(
            (now - timedelta(days=29))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        with self.connect() as conn:
            store_ids = [
                int(row["id"])
                for row in conn.execute("SELECT id FROM stores")
            ]
            result = {
                store_id: {
                    "in_sale": 0,
                    "warehouse": 0,
                    "today_revenue": 0,
                    "today_orders": 0,
                    "week_revenue": 0,
                    "month_revenue": 0,
                }
                for store_id in store_ids
            }
            for row in conn.execute(
                """
                SELECT store_id, COUNT(*) AS count
                FROM products INDEXED BY idx_products_store_status
                WHERE status=5
                GROUP BY store_id
                """
            ):
                if int(row["store_id"]) in result:
                    result[int(row["store_id"])]["in_sale"] = int(
                        row["count"] or 0
                    )
            for row in conn.execute(
                """
                SELECT store_id, COUNT(*) AS count
                FROM product_catalog
                GROUP BY store_id
                """
            ):
                if int(row["store_id"]) in result:
                    result[int(row["store_id"])]["warehouse"] = int(
                        row["count"] or 0
                    )
            for row in conn.execute(
                """
                SELECT
                    store_id,
                    COALESCE(SUM(CASE
                        WHEN create_time>=? THEN total_amount ELSE 0 END), 0)
                        AS today_revenue,
                    SUM(CASE WHEN create_time>=? THEN 1 ELSE 0 END)
                        AS today_orders,
                    COALESCE(SUM(CASE
                        WHEN create_time>=? THEN total_amount ELSE 0 END), 0)
                        AS week_revenue,
                    COALESCE(SUM(CASE
                        WHEN create_time>=? THEN total_amount ELSE 0 END), 0)
                        AS month_revenue
                FROM orders
                WHERE create_time>=? AND status IN (17, 20, 21, 30, 100)
                GROUP BY store_id
                """,
                (
                    today_start,
                    today_start,
                    week_start,
                    month_start,
                    month_start,
                ),
            ):
                store_id = int(row["store_id"])
                if store_id not in result:
                    continue
                result[store_id].update(
                    {
                        "today_revenue": int(row["today_revenue"] or 0),
                        "today_orders": int(row["today_orders"] or 0),
                        "week_revenue": int(row["week_revenue"] or 0),
                        "month_revenue": int(row["month_revenue"] or 0),
                    }
                )
        return result

    def store_metric_map(self) -> dict[int, dict[str, Any]]:
        metrics = self.store_summary_map()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value FROM app_settings
                WHERE key LIKE 'store_metrics_%'
                """
            ).fetchall()
            quota_rows = conn.execute(
                """
                SELECT key, value FROM app_settings
                WHERE key LIKE 'product_quota_%'
                """
            ).fetchall()
        for row in rows:
            key = str(row["key"])
            try:
                store_id = int(key.removeprefix("store_metrics_"))
                cached = json.loads(str(row["value"]))
            except (TypeError, ValueError):
                continue
            if store_id not in metrics or not isinstance(cached, dict):
                continue
            metrics[store_id].update(cached)
        for row in quota_rows:
            key = str(row["key"])
            try:
                store_id = int(key.removeprefix("product_quota_"))
                quota = json.loads(str(row["value"]))
            except (TypeError, ValueError):
                continue
            if store_id not in metrics or not isinstance(quota, dict):
                continue
            metrics[store_id]["quota_remaining"] = int(
                quota.get("remaining") or 0
            )
            metrics[store_id]["quota_total"] = int(
                quota.get("total") or 0
            )
        for values in metrics.values():
            values["product_total"] = max(
                int(values.get("product_total") or 0),
                int(values.get("in_sale") or 0)
                + int(values.get("warehouse") or 0),
            )
            values.setdefault(
                "gmv_today", values.get("today_revenue", 0)
            )
            values.setdefault(
                "gmv_7d", values.get("week_revenue", 0)
            )
            values.setdefault(
                "gmv_30d", values.get("month_revenue", 0)
            )
            values.setdefault(
                "orders_today", values.get("today_orders", 0)
            )
            values.setdefault(
                "refund_today", values.get("today_refund", 0)
            )
        return metrics

    def list_stores(
        self,
        search: str = "",
        *,
        on_database_opened: Callable[[str], None] | None = None,
    ) -> list[Store]:
        sql = """
            SELECT
                id, name, app_id, group_name, status, last_sync_at,
                created_at, web_status, web_status_detail,
                web_checked_at, web_logged_in_at
            FROM stores
        """
        args: list[Any] = []
        if search:
            sql += " WHERE name LIKE ? OR app_id LIKE ? OR group_name LIKE ?"
            term = f"%{search.strip()}%"
            args = [term, term, term]
        sql += " ORDER BY created_at, id"
        with self.connect() as conn:
            if on_database_opened is not None:
                for database in conn.execute("PRAGMA database_list"):
                    if database["name"] == "main":
                        on_database_opened(database["file"])
                        break
            rows = conn.execute(sql, args).fetchall()
        return [self._store_from_row(row) for row in rows]

    @staticmethod
    def _ensure_store_group(
        conn: sqlite3.Connection,
        group_name: str,
        now: str,
    ) -> str:
        normalized = str(group_name or "").strip()
        if normalized:
            conn.execute(
                """
                INSERT OR IGNORE INTO store_groups (name, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (normalized, now, now),
            )
        return normalized

    def list_store_groups(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT name FROM store_groups
                WHERE TRIM(name) <> ''
                ORDER BY name COLLATE NOCASE, name
                """
            ).fetchall()
        return [str(row["name"]) for row in rows]

    @staticmethod
    def direct_list_stores(database: Path) -> list[Store]:
        """Read stores with a standalone SQLite connection.

        This bypasses a stale ``Database`` instance while preserving the same
        schema conversion used by the normal store list.
        """
        path = Path(database)
        if not path.is_file():
            return []
        source = None
        try:
            source = sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=5,
                check_same_thread=False,
            )
            source.row_factory = sqlite3.Row
            rows = source.execute(
                """
                SELECT
                    id, name, app_id, group_name, status, last_sync_at,
                    created_at, web_status, web_status_detail,
                    web_checked_at, web_logged_in_at
                FROM stores
                ORDER BY created_at, id
                """
            ).fetchall()
            return [Database._store_from_row(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            if source is not None:
                source.close()

    def create_store_group(self, group_name: str) -> str:
        normalized = str(group_name or "").strip()
        if not normalized:
            raise ValueError("分组名称不能为空")
        now = _now()
        with self.connect() as conn:
            self._ensure_store_group(conn, normalized, now)
        return normalized

    def rename_store_group(self, old_name: str, new_name: str) -> None:
        old = str(old_name or "").strip()
        new = str(new_name or "").strip()
        if not old or not new:
            raise ValueError("分组名称不能为空")
        if old == new:
            self.create_store_group(new)
            return
        now = _now()
        with self.connect() as conn:
            self._ensure_store_group(conn, new, now)
            conn.execute(
                """
                UPDATE stores
                SET group_name=?, updated_at=?
                WHERE group_name=?
                """,
                (new, now, old),
            )
            conn.execute(
                "DELETE FROM store_groups WHERE name=?",
                (old,),
            )

    def delete_store_group(self, group_name: str) -> None:
        normalized = str(group_name or "").strip()
        if not normalized:
            return
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE stores
                SET group_name='', updated_at=?
                WHERE group_name=?
                """,
                (now, normalized),
            )
            conn.execute(
                "DELETE FROM store_groups WHERE name=?",
                (normalized,),
            )

    def get_store(self, store_id: int) -> Store | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        return self._store_from_row(row) if row else None

    def add_store(
        self,
        name: str,
        app_id: str,
        secret: str,
        group_name: str = "",
    ) -> int:
        name, app_id = name.strip(), app_id.strip()
        if not name or not app_id:
            raise ValueError("店铺名称和 AppID 不能为空")
        normalized_secret = secret.strip()
        protected = (
            self.protector.protect(normalized_secret)
            if normalized_secret
            else b""
        )
        status = "未验证" if normalized_secret else "仅网页登录"
        now = _now()
        with self.connect() as conn:
            self._ensure_store_group(conn, group_name, now)
            cursor = conn.execute(
                """
                INSERT INTO stores
                    (name, app_id, secret_protected, group_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    app_id,
                    protected,
                    group_name.strip(),
                    status,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def ensure_web_login_store(
        self,
        name: str,
        app_id: str,
        group_name: str = "扫码自动发现",
    ) -> tuple[int, bool]:
        """Create a web-only store discovered from the WeChat shop switcher.

        The web account is usable immediately for official backend pages.
        An empty protected secret is stored until the user later supplies the
        AppSecret needed by the public API.
        """
        app_id = app_id.strip()
        if not app_id:
            raise ValueError("AppID 不能为空")
        display_name = name.strip() or f"店铺{app_id[-4:]}"
        now = _now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, name FROM stores WHERE app_id=?",
                (app_id,),
            ).fetchone()
            if row is not None:
                store_id = int(row["id"])
                current_name = str(row["name"] or "").strip()
                if (
                    current_name.startswith("店铺")
                    or current_name == app_id
                ) and display_name != current_name:
                    conn.execute(
                        "UPDATE stores SET name=?, updated_at=? WHERE id=?",
                        (display_name, now, store_id),
                    )
                return store_id, False
            protected = b""
            cursor = conn.execute(
                """
                INSERT INTO stores (
                    name, app_id, secret_protected, group_name,
                    status, web_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '仅网页登录', '未登录', ?, ?)
                """,
                (
                    display_name,
                    app_id,
                    protected,
                    group_name.strip(),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid), True

    def upsert_stores(
        self,
        records: list[tuple[str, str, str]],
        group_name: str = "",
    ) -> tuple[int, int, list[str]]:
        """Create or refresh many stores in one transaction."""
        prepared: dict[str, tuple[str, str, bytes, bool]] = {}
        errors: list[str] = []
        supplied_group = group_name.strip()
        for raw_name, raw_app_id, raw_secret in records:
            name = str(raw_name or "").strip()
            app_id = str(raw_app_id or "").strip()
            secret = str(raw_secret or "").strip()
            if not app_id:
                errors.append(f"{name or '未知店铺'}: AppID 为空")
                continue
            try:
                protected = self.protector.protect(secret) if secret else b""
            except Exception as exc:
                errors.append(f"{app_id}: {exc}")
                continue
            prepared[app_id] = (name, app_id, protected, bool(secret))

        created = 0
        updated = 0
        now = _now()
        with self.connect() as conn:
            self._ensure_store_group(conn, supplied_group, now)
            existing = {
                str(row["app_id"]): row
                for row in conn.execute(
                    "SELECT id, app_id, name, group_name FROM stores"
                )
            }
            for app_id, (
                provided_name,
                _app_id,
                protected,
                has_secret,
            ) in prepared.items():
                current = existing.get(app_id)
                if current is None:
                    conn.execute(
                        """
                        INSERT INTO stores (
                            name, app_id, secret_protected, group_name,
                            status, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            provided_name or f"店铺{app_id[-4:]}",
                            app_id,
                            protected,
                            supplied_group,
                            "未验证" if has_secret else "仅网页登录",
                            now,
                            now,
                        ),
                    )
                    created += 1
                    continue
                preserved_name = str(current["name"] or "").strip()
                preserved_group = str(current["group_name"] or "").strip()
                if has_secret:
                    conn.execute(
                        """
                        UPDATE stores
                        SET name=?, group_name=?, secret_protected=?,
                            status='未验证', updated_at=?
                        WHERE id=?
                        """,
                        (
                            provided_name or preserved_name,
                            supplied_group or preserved_group,
                            protected,
                            now,
                            int(current["id"]),
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE stores
                        SET name=?, group_name=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            provided_name or preserved_name,
                            supplied_group or preserved_group,
                            now,
                            int(current["id"]),
                        ),
                    )
                updated += 1
        return created, updated, errors

    def update_store(
        self,
        store_id: int,
        name: str,
        group_name: str,
        secret: str | None = None,
    ) -> None:
        now = _now()
        with self.connect() as conn:
            self._ensure_store_group(conn, group_name, now)
            if secret is not None and secret.strip():
                conn.execute(
                    """
                    UPDATE stores
                    SET name=?, group_name=?, secret_protected=?, status='未验证', updated_at=?
                    WHERE id=?
                    """,
                    (
                        name.strip(),
                        group_name.strip(),
                        self.protector.protect(secret.strip()),
                        now,
                        store_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE stores SET name=?, group_name=?, updated_at=? WHERE id=?",
                    (name.strip(), group_name.strip(), now, store_id),
                )

    def set_store_groups(
        self,
        store_ids: list[int],
        group_name: str,
    ) -> int:
        ids = [int(store_id) for store_id in store_ids]
        if not ids:
            return 0
        now = _now()
        with self.connect() as conn:
            self._ensure_store_group(conn, group_name, now)
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(
                    f"""
                    UPDATE stores
                    SET group_name=?, updated_at=?
                    WHERE id IN ({placeholders})
                    """,
                    (group_name.strip(), now, *chunk),
                )
        return len(ids)

    def get_store_secret(self, store_id: int) -> tuple[Store, str]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        if row is None:
            raise KeyError("店铺不存在")
        protected = row["secret_protected"]
        secret = self.protector.unprotect(protected) if protected else ""
        return self._store_from_row(row), secret

    def set_store_status(self, store_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE stores SET status=?, updated_at=? WHERE id=?",
                (status, _now(), store_id),
            )

    def set_web_session(
        self,
        store_id: int,
        cookies: dict[str, Any],
        status: str,
        detail: str,
    ) -> None:
        protected = self.protector.protect(
            json.dumps(cookies, ensure_ascii=False)
        )
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE stores
                SET web_cookies_protected=?,
                    web_status=?,
                    web_status_detail=?,
                    web_checked_at=?,
                    web_logged_in_at=?,
                    web_session_updated_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    protected,
                    status,
                    detail[:500],
                    now,
                    now,
                    now,
                    now,
                    store_id,
                ),
            )

    def update_web_status(
        self,
        store_id: int,
        status: str,
        detail: str = "",
    ) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE stores
                SET web_status=?,
                    web_status_detail=?,
                    web_checked_at=?,
                    web_session_updated_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (status, detail[:500], now, now, now, store_id),
            )

    def get_web_cookies(self, store_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT web_cookies_protected FROM stores WHERE id=?",
                (store_id,),
            ).fetchone()
        if not row or not row["web_cookies_protected"]:
            return None
        try:
            payload = json.loads(
                self.protector.unprotect(row["web_cookies_protected"])
            )
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def export_store_accounts(self) -> dict[str, Any]:
        """Build a portable account snapshot for encrypted cloud sync."""
        stores: list[dict[str, Any]] = []
        with self.connect() as conn:
            session_times = {
                int(row["id"]): str(
                    row["web_session_updated_at"] or ""
                )
                for row in conn.execute(
                    "SELECT id, web_session_updated_at FROM stores"
                ).fetchall()
            }
        for store in self.list_stores():
            secret = ""
            try:
                _, secret = self.get_store_secret(store.id)
            except Exception:
                pass
            cookies = self.get_web_cookies(store.id)
            stores.append(
                {
                    "app_id": store.app_id,
                    "name": store.name,
                    "group_name": store.group_name,
                    "secret": secret,
                    "web_status": store.web_status,
                    "web_status_detail": store.web_status_detail,
                    "web_session_updated_at": session_times.get(
                        store.id,
                        "",
                    ),
                    "cookies": cookies,
                    "created_at": store.created_at,
                }
            )
        return {
            "type": "langlang-store-accounts",
            "version": 1,
            "exported_at": _now(),
            "groups": self.list_store_groups(),
            "stores": stores,
        }

    def import_store_accounts(
        self,
        snapshot: dict[str, Any],
    ) -> dict[str, int]:
        """Merge portable store bindings without deleting local accounts."""
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("type") != "langlang-store-accounts"
        ):
            raise ValueError("账户店铺数据格式不正确")
        incoming = snapshot.get("stores")
        if not isinstance(incoming, list):
            raise ValueError("账户店铺数据缺少 stores")
        groups = snapshot.get("groups")
        if isinstance(groups, list):
            for group_name in groups:
                normalized = str(group_name or "").strip()
                if normalized:
                    self.create_store_group(normalized)
        existing = {
            store.app_id: store
            for store in self.list_stores()
        }
        created = 0
        updated = 0
        logins = 0
        for item in incoming:
            if not isinstance(item, dict):
                continue
            app_id = str(item.get("app_id") or "").strip()
            if not app_id:
                continue
            name = str(item.get("name") or app_id).strip()
            group_name = str(item.get("group_name") or "").strip()
            secret = str(item.get("secret") or "")
            current = existing.get(app_id)
            current_session_time = ""
            current_web_status = "未登录"
            if current is not None:
                current_web_status = current.web_status
                with self.connect() as conn:
                    row = conn.execute(
                        """
                        SELECT web_session_updated_at
                        FROM stores
                        WHERE id=?
                        """,
                        (current.id,),
                    ).fetchone()
                current_session_time = str(
                    row["web_session_updated_at"] or ""
                    if row
                    else ""
                )
            if current is None:
                store_id = self.add_store(
                    name,
                    app_id,
                    secret,
                    group_name,
                )
                created += 1
            else:
                store_id = current.id
                self.update_store(
                    store_id,
                    name or current.name,
                    group_name,
                    secret or None,
                )
                updated += 1
            incoming_session_time = str(
                item.get("web_session_updated_at") or ""
            )
            apply_login = (
                _incoming_session_is_not_older(
                    incoming_session_time,
                    current_session_time,
                )
                if incoming_session_time
                else current_web_status != "已登录"
            )
            cookies = item.get("cookies")
            incoming_status = str(item.get("web_status") or "已登录")
            incoming_detail = str(
                item.get("web_status_detail")
                or "账户同步恢复"
            )
            if not apply_login:
                continue
            if "cookies" in item and incoming_status == "未登录":
                self.clear_web_session(
                    store_id,
                    incoming_detail,
                )
            elif isinstance(cookies, dict) and cookies:
                self.set_web_session(
                    store_id,
                    cookies,
                    incoming_status,
                    incoming_detail,
                )
                logins += 1
        return {
            "created": created,
            "updated": updated,
            "logins": logins,
        }

    def restore_store_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Restore store bindings after a failed cloud transaction.

        Local integer IDs are preserved where possible because orders and other
        business rows reference them.  No plaintext credential is written
        outside the existing DPAPI-protected SQLite columns.
        """
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("type") != "langlang-store-accounts"
        ):
            raise ValueError("店铺回滚快照格式不正确")
        incoming = snapshot.get("stores")
        if not isinstance(incoming, list):
            raise ValueError("店铺回滚快照缺少 stores")
        wanted = {
            str(item.get("app_id") or "").strip(): item
            for item in incoming
            if isinstance(item, dict)
            and str(item.get("app_id") or "").strip()
        }
        current = {
            store.app_id: store
            for store in self.list_stores()
        }
        for app_id, store in current.items():
            if app_id not in wanted:
                self.delete_store(store.id)

        for app_id, item in wanted.items():
            name = str(item.get("name") or app_id).strip()
            group_name = str(item.get("group_name") or "").strip()
            secret = str(item.get("secret") or "")
            store = current.get(app_id)
            if store is None:
                store_id = self.add_store(
                    name,
                    app_id,
                    secret,
                    group_name,
                )
            else:
                store_id = store.id
                self.update_store(
                    store_id,
                    name,
                    group_name,
                    secret or None,
                )
            cookies = item.get("cookies")
            if isinstance(cookies, dict) and cookies:
                self.set_web_session(
                    store_id,
                    cookies,
                    str(item.get("web_status") or "已登录"),
                    str(item.get("web_status_detail") or "事务回滚恢复"),
                )
            else:
                self.clear_web_session(
                    store_id,
                    str(item.get("web_status_detail") or "事务回滚恢复"),
                )

        groups = snapshot.get("groups")
        if isinstance(groups, list):
            wanted_groups = {
                str(name or "").strip()
                for name in groups
                if str(name or "").strip()
            }
            for group_name in wanted_groups:
                self.create_store_group(group_name)
            for group_name in self.list_store_groups():
                if group_name not in wanted_groups:
                    self.delete_store_group(group_name)

    def clear_web_session(self, store_id: int, detail: str = "已退出登录") -> None:
        self.update_web_status(store_id, "未登录", detail)
        with self.connect() as conn:
            conn.execute(
                "UPDATE stores SET web_cookies_protected=NULL WHERE id=?",
                (store_id,),
            )

    def clear_selected_web_sessions(
        self,
        store_ids: list[int],
        detail: str = "已离线所选店铺",
    ) -> dict[str, int]:
        """Clear login cookies only for selected stores that are logged in."""
        normalized_ids = sorted(
            {
                int(store_id)
                for store_id in store_ids
                if store_id is not None
            }
        )
        if not normalized_ids:
            return {"cleared": 0, "skipped": 0}
        placeholders = ",".join("?" for _ in normalized_ids)
        now = _now()
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE stores
                SET web_cookies_protected=NULL,
                    web_status='未登录',
                    web_status_detail=?,
                    web_checked_at=?,
                    web_session_updated_at=?,
                    updated_at=?
                WHERE id IN ({placeholders})
                  AND web_status='已登录'
                """,
                (detail[:500], now, now, now, *normalized_ids),
            )
            cleared = max(0, int(cursor.rowcount))
        return {
            "cleared": cleared,
            "skipped": len(normalized_ids) - cleared,
        }

    def clear_all_web_logins(
        self,
        detail: str = "账号中心批量清理",
    ) -> dict[str, int]:
        """Clear every shop and procurement web login in one transaction."""
        now = _now()
        with self.connect() as conn:
            stores = conn.execute(
                """
                UPDATE stores
                SET web_cookies_protected=NULL,
                    web_status='未登录',
                    web_status_detail=?,
                    web_checked_at=?,
                    web_session_updated_at=?,
                    updated_at=?
                WHERE web_status='已登录'
                   OR web_cookies_protected IS NOT NULL
                """,
                (detail[:500], now, now, now),
            )
            procurement = conn.execute(
                """
                UPDATE procurement_accounts
                SET status='未登录',
                    last_error='登录态已清理'
                WHERE status<>'未登录'
                   OR EXISTS (
                       SELECT 1
                       FROM procurement_sessions AS session
                       WHERE session.account_id=procurement_accounts.id
                   )
                """
            )
            conn.execute("DELETE FROM procurement_sessions")
        return {
            "stores_cleared": max(0, int(stores.rowcount)),
            "procurement_cleared": max(0, int(procurement.rowcount)),
        }

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key=?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, value, _now()),
            )

    def delete_setting(self, key: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM app_settings WHERE key=?",
                (key,),
            )

    def apply_remote_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (str(key), str(value), _now()),
            )

    def adjust_upload_metrics(
        self,
        store_id: int,
        product_delta: int = 1,
        quota_delta: int = 0,
        listing_delta: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            quota_key = f"product_quota_{store_id}"
            metrics_key = f"store_metrics_{store_id}"
            quota_row = conn.execute(
                "SELECT value FROM app_settings WHERE key=?",
                (quota_key,),
            ).fetchone()
            metrics_row = conn.execute(
                "SELECT value FROM app_settings WHERE key=?",
                (metrics_key,),
            ).fetchone()
            try:
                quota = json.loads(
                    quota_row["value"] if quota_row else "{}"
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                quota = {}
            try:
                metrics = json.loads(
                    metrics_row["value"] if metrics_row else "{}"
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                metrics = {}
            quota = quota if isinstance(quota, dict) else {}
            metrics = metrics if isinstance(metrics, dict) else {}
            remaining = max(
                0,
                int(quota.get("remaining") or 0) + quota_delta,
            )
            total = int(quota.get("total") or 0)
            quota["remaining"] = remaining
            quota["total"] = total
            metrics["quota_remaining"] = remaining
            metrics["quota_total"] = total
            metrics["product_total"] = max(
                0,
                int(metrics.get("product_total") or 0)
                + product_delta,
            )
            if listing_delta:
                metrics["in_sale"] = max(
                    0,
                    int(metrics.get("in_sale") or 0)
                    + listing_delta,
                )
            metrics["_pending_refresh"] = True
            metrics["upload_updated_at"] = _now()
            for key, value in (
                (quota_key, quota),
                (metrics_key, metrics),
            ):
                conn.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (
                        key,
                        json.dumps(value, ensure_ascii=False),
                        _now(),
                    ),
                )

    def data_counts(self) -> dict[str, int]:
        """Return summary counts without loading full business tables."""
        today_start = int(
            datetime.now()
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM stores) AS stores,
                    (SELECT COUNT(*) FROM stores
                        WHERE web_status='已登录') AS logged_stores,
                    (SELECT COUNT(*) FROM orders) AS orders,
                    (SELECT COUNT(*) FROM orders
                        WHERE create_time>=?) AS today_orders,
                    (SELECT COUNT(*) FROM orders
                        WHERE status=20) AS pending_shipping,
                    (SELECT COUNT(*) FROM products) AS products,
                    (SELECT COUNT(*) FROM product_catalog
                        WHERE state='done') AS catalog_done,
                    (SELECT COUNT(*) FROM after_sales) AS after_sales,
                    (SELECT COUNT(*) FROM procurement_accounts)
                        AS procurement_accounts,
                    (SELECT COUNT(*) FROM automation_tasks)
                        AS automation_tasks
                """,
                (today_start,),
            ).fetchone()
        return {
            key: int(row[key] or 0)
            for key in (
                "stores",
                "logged_stores",
                "orders",
                "today_orders",
                "pending_shipping",
                "products",
                "catalog_done",
                "after_sales",
                "procurement_accounts",
                "automation_tasks",
            )
        }

    def violation_blacklist_ids(self) -> set[str]:
        try:
            rows = json.loads(self.get_setting("violation_blacklist", "[]"))
        except json.JSONDecodeError:
            return set()
        if not isinstance(rows, list):
            return set()
        return {
            str(row.get("product_id"))
            for row in rows
            if isinstance(row, dict) and row.get("product_id")
        }

    def upsert_store_profile(self, store_id: int, info: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO store_profiles (
                    store_id, nickname, headimg_url, subject_type, status,
                    username, is_local_life, open_timestamp, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                    nickname=excluded.nickname,
                    headimg_url=excluded.headimg_url,
                    subject_type=excluded.subject_type,
                    status=excluded.status,
                    username=excluded.username,
                    is_local_life=excluded.is_local_life,
                    open_timestamp=excluded.open_timestamp,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    store_id,
                    str(info.get("nickname") or ""),
                    str(info.get("headimg_url") or ""),
                    str(info.get("subject_type") or ""),
                    str(info.get("status") or ""),
                    str(info.get("username") or ""),
                    int(info.get("is_local_life") or 0),
                    int(info.get("open_timestamp") or 0),
                    json.dumps(info, ensure_ascii=False),
                    _now(),
                ),
            )

    def get_store_profile(self, store_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM store_profiles WHERE store_id=?",
                (store_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_store_identity(self, store_id: int) -> dict[str, str]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    s.id AS store_id,
                    s.name AS store_name,
                    COALESCE(p.nickname, '') AS nickname,
                    COALESCE(p.username, '') AS username
                FROM stores s
                LEFT JOIN store_profiles p ON p.store_id=s.id
                WHERE s.id=?
                """,
                (store_id,),
            ).fetchone()
        return dict(row) if row else {}

    def list_store_identities(self) -> list[dict[str, str]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.id AS store_id,
                    s.name AS store_name,
                    COALESCE(p.nickname, '') AS nickname,
                    COALESCE(p.username, '') AS username
                FROM stores s
                LEFT JOIN store_profiles p ON p.store_id=s.id
                ORDER BY s.id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_after_sale(self, store_id: int, payload: dict[str, Any]) -> None:
        from .models import AFTER_SALE_STATUS

        status = str(payload.get("status") or "")
        refund_info = payload.get("refund_info") or {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO after_sales (
                    store_id, after_sale_order_id, order_id, status, status_text,
                    after_sale_type, reason_text, refund_amount, create_time,
                    update_time, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, after_sale_order_id) DO UPDATE SET
                    order_id=excluded.order_id,
                    status=excluded.status,
                    status_text=excluded.status_text,
                    after_sale_type=excluded.after_sale_type,
                    reason_text=excluded.reason_text,
                    refund_amount=excluded.refund_amount,
                    create_time=excluded.create_time,
                    update_time=excluded.update_time,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    store_id,
                    str(payload.get("after_sale_order_id") or ""),
                    str(payload.get("order_id") or ""),
                    status,
                    AFTER_SALE_STATUS.get(status, status),
                    str(payload.get("type") or ""),
                    str(payload.get("reason_text") or ""),
                    int(refund_info.get("amount") or 0),
                    int(payload.get("create_time") or 0),
                    int(payload.get("update_time") or 0),
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )

    def list_after_sales(self, store_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM after_sales"
        args: tuple[Any, ...] = ()
        if store_id is not None:
            sql += " WHERE store_id=?"
            args = (store_id,)
        sql += " ORDER BY update_time DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def delete_after_sale(
        self,
        store_id: int,
        after_sale_order_id: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM after_sales
                WHERE store_id=? AND after_sale_order_id=?
                """,
                (int(store_id), str(after_sale_order_id)),
            )

    def create_transfer_case(self, payload: dict[str, Any]) -> int:
        now = _now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO transfer_cases (
                    case_type, store_id, order_id, procurement_order_id,
                    source_order_no, transfer_order_no, work_order_no,
                    tracking_no, pickup_code, warehouse, status, status_text,
                    note, source_created_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("case_type") or "internal"),
                    int(payload["store_id"])
                    if payload.get("store_id") not in (None, "")
                    else None,
                    str(payload.get("order_id") or ""),
                    int(payload["procurement_order_id"])
                    if payload.get("procurement_order_id") not in (None, "")
                    else None,
                    str(payload.get("source_order_no") or ""),
                    str(payload.get("transfer_order_no") or ""),
                    str(payload.get("work_order_no") or ""),
                    str(payload.get("tracking_no") or ""),
                    str(payload.get("pickup_code") or ""),
                    str(payload.get("warehouse") or ""),
                    str(payload.get("status") or "待处理"),
                    str(payload.get("status_text") or ""),
                    str(payload.get("note") or ""),
                    str(payload.get("source_created_at") or ""),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_transfer_cases(
        self,
        *,
        case_type: str | None = None,
        store_id: int | None = None,
        search: str = "",
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT t.*, s.name AS store_name, p.platform,
                   p.external_order_id, p.mail_no, p.goods_title,
                   p.pay_amount, p.status_text AS procurement_status_text,
                   p.updated_at AS procurement_updated_at
            FROM transfer_cases t
            LEFT JOIN stores s ON s.id=t.store_id
            LEFT JOIN procurement_orders p ON p.id=t.procurement_order_id
            WHERE 1=1
        """
        args: list[Any] = []
        if case_type:
            sql += " AND t.case_type=?"
            args.append(str(case_type))
        if store_id is not None:
            sql += " AND t.store_id=?"
            args.append(int(store_id))
        term = search.strip()
        if term:
            pattern = f"%{term}%"
            sql += """
                AND (
                    t.order_id LIKE ? OR t.source_order_no LIKE ?
                    OR t.transfer_order_no LIKE ? OR t.work_order_no LIKE ?
                    OR t.tracking_no LIKE ? OR t.pickup_code LIKE ?
                    OR p.external_order_id LIKE ?
                )
            """
            args.extend((pattern,) * 7)
        sql += " ORDER BY t.updated_at DESC, t.id DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def update_transfer_case(
        self,
        case_id: int,
        **changes: Any,
    ) -> None:
        allowed = {
            "case_type",
            "store_id",
            "order_id",
            "procurement_order_id",
            "source_order_no",
            "transfer_order_no",
            "work_order_no",
            "tracking_no",
            "pickup_code",
            "warehouse",
            "status",
            "status_text",
            "note",
            "source_created_at",
        }
        updates = {
            key: value
            for key, value in changes.items()
            if key in allowed
        }
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE transfer_cases
                SET {assignments}, updated_at=?
                WHERE id=?
                """,
                (*updates.values(), _now(), int(case_id)),
            )

    def upsert_product_purchase_link(
        self,
        store_id: int,
        product_id: str,
        payload: dict[str, Any],
    ) -> int:
        now = _now()
        link_id = int(payload.get("id") or 0)
        source_skus = payload.get("source_skus")
        source_skus_json = (
            json.dumps(source_skus, ensure_ascii=False)
            if isinstance(source_skus, list)
            else str(payload.get("source_skus_json") or "[]")
        )
        values = (
            str(payload.get("source_url") or ""),
            str(payload.get("platform") or ""),
            1 if payload.get("use_transfer_warehouse") else 0,
            float(payload.get("commission_pct") or 0),
            str(payload.get("note") or ""),
            json.dumps(
                payload.get("address_info")
                if isinstance(payload.get("address_info"), dict)
                else {},
                ensure_ascii=False,
            ),
            str(payload.get("source_product_id") or ""),
            str(payload.get("source_title") or ""),
            source_skus_json,
            now,
        )
        with self.connect() as conn:
            if link_id:
                existing = conn.execute(
                    """
                    SELECT id FROM product_purchase_links
                    WHERE id=? AND store_id=? AND product_id=?
                    """,
                    (link_id, int(store_id), str(product_id)),
                ).fetchone()
                if existing is None:
                    raise ValueError("采购链接不存在或不属于当前商品")
                conn.execute(
                    """
                    UPDATE product_purchase_links
                    SET source_url=?, platform=?, use_transfer_warehouse=?,
                        commission_pct=?, note=?, address_info_json=?,
                        source_product_id=?,
                        source_title=?, source_skus_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (*values, link_id),
                )
                return link_id
            cursor = conn.execute(
                """
                INSERT INTO product_purchase_links (
                    store_id, product_id, source_url, platform,
                    use_transfer_warehouse, commission_pct, note,
                    address_info_json,
                    source_product_id, source_title, source_skus_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(store_id),
                    str(product_id),
                    *values,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_product_purchase_links(
        self,
        store_id: int,
        product_id: str,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM product_purchase_links
                WHERE store_id=? AND product_id=?
                ORDER BY id
                """,
                (int(store_id), str(product_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_product_purchase_link(
        self,
        link_id: int,
        *,
        store_id: int,
        product_id: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM product_purchase_links
                WHERE id=? AND store_id=? AND product_id=?
                """,
                (int(link_id), int(store_id), str(product_id)),
            )

    def replace_product_sku_mappings(
        self,
        store_id: int,
        product_id: str,
        mappings: list[dict[str, Any]],
    ) -> None:
        now = _now()
        allowed_links = {
            int(row["id"])
            for row in self.list_product_purchase_links(
                store_id,
                product_id,
            )
        }
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM product_sku_mappings
                WHERE store_id=? AND product_id=?
                """,
                (int(store_id), str(product_id)),
            )
            for item in mappings:
                store_sku_id = str(item.get("store_sku_id") or "").strip()
                link_id = int(item.get("purchase_link_id") or 0)
                if not store_sku_id or link_id not in allowed_links:
                    continue
                attrs = item.get("purchase_attrs") or []
                conn.execute(
                    """
                    INSERT INTO product_sku_mappings (
                        store_id, product_id, store_sku_id,
                        purchase_link_id, purchase_sku_id,
                        purchase_attrs_json, purchase_price,
                        purchase_image, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(store_id),
                        str(product_id),
                        store_sku_id,
                        link_id,
                        str(item.get("purchase_sku_id") or ""),
                        json.dumps(attrs, ensure_ascii=False),
                        int(item.get("purchase_price") or 0),
                        str(item.get("purchase_image") or ""),
                        now,
                    ),
                )

    def list_product_sku_mappings(
        self,
        store_id: int,
        product_id: str,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM product_sku_mappings
                WHERE store_id=? AND product_id=?
                ORDER BY store_sku_id
                """,
                (int(store_id), str(product_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def product_purchase_link_map(self) -> dict[tuple[int, str], dict[str, Any]]:
        sql = """
            SELECT l.store_id, l.product_id,
                   COUNT(DISTINCT l.id) AS link_count,
                   COUNT(DISTINCT m.store_sku_id) AS mapping_count,
                   COALESCE(
                       GROUP_CONCAT(DISTINCT NULLIF(l.source_product_id, '')),
                       ''
                   ) AS source_product_ids
            FROM product_purchase_links l
            LEFT JOIN product_sku_mappings m
                ON m.purchase_link_id=l.id
               AND m.store_id=l.store_id
               AND m.product_id=l.product_id
            GROUP BY l.store_id, l.product_id
        """
        with self.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return {
            (int(row["store_id"]), str(row["product_id"])): {
                "link_count": int(row["link_count"] or 0),
                "mapping_count": int(row["mapping_count"] or 0),
                "source_product_ids": [
                    value
                    for value in str(
                        row["source_product_ids"] or ""
                    ).split(",")
                    if value
                ],
            }
            for row in rows
        }

    def sync_auto_dispatch_tasks(self) -> int:
        now = _now()
        with self.connect() as conn:
            link_rows = conn.execute(
                """
                SELECT * FROM product_purchase_links
                ORDER BY store_id, product_id, id
                """
            ).fetchall()
            mapping_rows = conn.execute(
                """
                SELECT * FROM product_sku_mappings
                ORDER BY store_id, product_id, store_sku_id
                """
            ).fetchall()
            items = conn.execute(
                """
                SELECT oi.store_id, oi.order_id, oi.product_id, oi.sku_id,
                       oi.sku_count
                FROM order_items oi
                JOIN orders o
                  ON o.store_id=oi.store_id AND o.order_id=oi.order_id
                WHERE o.status=20
                ORDER BY o.create_time DESC
                """
            ).fetchall()
            links: dict[tuple[int, str], list[sqlite3.Row]] = {}
            for row in link_rows:
                links.setdefault(
                    (int(row["store_id"]), str(row["product_id"])),
                    [],
                ).append(row)
            mappings: dict[tuple[int, str, str], sqlite3.Row] = {
                (
                    int(row["store_id"]),
                    str(row["product_id"]),
                    str(row["store_sku_id"]),
                ): row
                for row in mapping_rows
            }
            created = 0
            for item in items:
                store_id = int(item["store_id"])
                product_id = str(item["product_id"] or "")
                if not product_id:
                    continue
                product_links = links.get((store_id, product_id), [])
                if not product_links:
                    continue
                mapping = mappings.get(
                    (store_id, product_id, str(item["sku_id"] or ""))
                )
                selected = None
                if mapping is not None:
                    selected = next(
                        (
                            row
                            for row in product_links
                            if int(row["id"])
                            == int(mapping["purchase_link_id"])
                        ),
                        None,
                    )
                elif len(product_links) == 1:
                    selected = product_links[0]
                if selected is None:
                    continue
                cursor = conn.execute(
                    """
                    INSERT INTO order_dispatch_tasks (
                        store_id, order_id, product_id, store_sku_id,
                        purchase_link_id, source_url, platform, quantity,
                        status, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)
                    ON CONFLICT(
                        store_id, order_id, product_id, store_sku_id
                    ) DO UPDATE SET
                        purchase_link_id=excluded.purchase_link_id,
                        source_url=excluded.source_url,
                        platform=excluded.platform,
                        quantity=excluded.quantity,
                        updated_at=excluded.updated_at
                    """,
                    (
                        store_id,
                        str(item["order_id"]),
                        product_id,
                        str(item["sku_id"] or ""),
                        int(selected["id"]),
                        str(selected["source_url"] or ""),
                        str(selected["platform"] or ""),
                        max(1, int(item["sku_count"] or 1)),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount:
                    created += 1
            return created

    def list_order_dispatch_tasks(
        self,
        store_id: int | None = None,
        order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT t.*, l.note AS link_note, l.commission_pct,
                   l.use_transfer_warehouse
            FROM order_dispatch_tasks t
            LEFT JOIN product_purchase_links l ON l.id=t.purchase_link_id
            WHERE 1=1
        """
        args: list[Any] = []
        if store_id is not None:
            sql += " AND t.store_id=?"
            args.append(int(store_id))
        if order_id is not None:
            sql += " AND t.order_id=?"
            args.append(str(order_id))
        sql += " ORDER BY t.updated_at DESC, t.id DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def order_dispatch_map(
        self,
        store_id: int | None = None,
    ) -> dict[tuple[int, str], list[dict[str, Any]]]:
        result: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in self.list_order_dispatch_tasks(store_id=store_id):
            key = (int(row["store_id"]), str(row["order_id"]))
            result.setdefault(key, []).append(row)
        return result

    def update_order_dispatch_task(
        self,
        task_id: int,
        *,
        status: str,
        error: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE order_dispatch_tasks
                SET status=?, error=?, updated_at=?
                WHERE id=?
                """,
                (
                    str(status),
                    str(error)[:1000],
                    _now(),
                    int(task_id),
                ),
            )

    def record_activity_operation(
        self,
        action: str,
        status: str,
        store_id: int | None = None,
        store_name: str = "",
        activity_id: int | None = None,
        item_count: int = 0,
        message: str = "",
        detail: Any = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO activity_operation_runs (
                    action, store_id, store_name, activity_id, status,
                    item_count, message, detail_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(action),
                    store_id,
                    store_name,
                    activity_id,
                    str(status),
                    int(item_count),
                    str(message)[:2000],
                    json.dumps(detail or {}, ensure_ascii=False),
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_activity_operations(
        self,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM activity_operation_runs
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_after_sale(
        self,
        store_id: int,
        after_sale_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM after_sales WHERE store_id=? AND after_sale_order_id=?",
                (store_id, after_sale_id),
            ).fetchone()
        return dict(row) if row else None

    def upsert_analytics_day(
        self,
        store_id: int,
        business_date: str,
        data: dict[str, Any],
    ) -> None:
        def number(key: str) -> int:
            try:
                return int(data.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analytics_daily (
                    store_id, business_date, pay_gmv, pay_uv, pay_order_cnt,
                    pay_refund_gmv, live_pay_gmv, feed_pay_gmv, product_click_uv,
                    raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, business_date) DO UPDATE SET
                    pay_gmv=excluded.pay_gmv,
                    pay_uv=excluded.pay_uv,
                    pay_order_cnt=excluded.pay_order_cnt,
                    pay_refund_gmv=excluded.pay_refund_gmv,
                    live_pay_gmv=excluded.live_pay_gmv,
                    feed_pay_gmv=excluded.feed_pay_gmv,
                    product_click_uv=excluded.product_click_uv,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    store_id,
                    business_date,
                    number("pay_gmv"),
                    number("pay_uv"),
                    number("pay_order_cnt"),
                    number("pay_refund_gmv"),
                    number("live_pay_gmv"),
                    number("feed_pay_gmv"),
                    number("product_click_uv"),
                    json.dumps(data, ensure_ascii=False),
                    _now(),
                ),
            )

    def get_analytics_day(
        self,
        store_id: int,
        business_date: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM analytics_daily
                WHERE store_id=? AND business_date=?
                """,
                (store_id, business_date),
            ).fetchone()
        return dict(row) if row else None

    def list_analytics_days(
        self,
        store_id: int,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM analytics_daily
                WHERE store_id=?
                ORDER BY business_date DESC
                LIMIT ?
                """,
                (store_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_coupon(self, store_id: int, payload: dict[str, Any]) -> None:
        coupon_info = payload.get("coupon_info") or {}
        discount = coupon_info.get("discount_info") or {}
        receive = coupon_info.get("receive_info") or {}
        stock = payload.get("stock_info") or {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO coupons (
                    store_id, coupon_id, name, coupon_type, status,
                    discount_fee, discount_num, total_num, receive_num,
                    used_num, start_time, end_time, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, coupon_id) DO UPDATE SET
                    name=excluded.name,
                    coupon_type=excluded.coupon_type,
                    status=excluded.status,
                    discount_fee=excluded.discount_fee,
                    discount_num=excluded.discount_num,
                    total_num=excluded.total_num,
                    receive_num=excluded.receive_num,
                    used_num=excluded.used_num,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    store_id,
                    str(payload.get("coupon_id") or ""),
                    str(coupon_info.get("name") or ""),
                    int(payload.get("type") or 0),
                    int(payload.get("status") or 0),
                    int(discount.get("discount_fee") or 0),
                    int(discount.get("discount_num") or 0),
                    int(receive.get("total_num") or 0),
                    int(stock.get("receive_num") or 0),
                    int(stock.get("used_num") or 0),
                    int(receive.get("start_time") or 0),
                    int(receive.get("end_time") or 0),
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )

    def list_coupons(self, store_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM coupons WHERE store_id=? ORDER BY status, coupon_id DESC",
                (store_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_order_sensitive(
        self,
        store_id: int,
        order_id: str,
        buyer_name: str,
        phone: str,
        address: str,
        address_parts: dict[str, str] | None = None,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM orders WHERE store_id=? AND order_id=?",
                (store_id, order_id),
            ).fetchone()
            raw_json = row["raw_json"] if row else "{}"
            try:
                payload = json.loads(raw_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            address_info = (
                payload.setdefault("order_detail", {})
                .setdefault("delivery_info", {})
                .setdefault("address_info", {})
            )
            address_info["user_name"] = buyer_name
            address_info["tel_number"] = phone
            if address_parts:
                address_info["province_name"] = address_parts.get("province", "")
                address_info["city_name"] = address_parts.get("city", "")
                address_info["county_name"] = address_parts.get("county", "")
                address_info["street_name"] = address_parts.get("street", "")
                address_info["detail_info"] = address_parts.get("address", "")
            else:
                address_info["detail_info"] = address
            conn.execute(
                """
                UPDATE orders
                SET buyer_name=?, phone=?, address=?, raw_json=?, synced_at=?
                WHERE store_id=? AND order_id=?
                """,
                (
                    buyer_name,
                    phone,
                    address,
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                    store_id,
                    order_id,
                ),
            )

    def touch_store_sync(self, store_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE stores SET last_sync_at=?, updated_at=? WHERE id=?",
                (_now(), _now(), store_id),
            )

    def delete_store(self, store_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM stores WHERE id = ?", (store_id,))

    def upsert_order(self, order: Order) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    store_id, order_id, status, status_text, create_time, update_time,
                    pay_time, total_amount, product_amount, freight_amount,
                    discount_amount, buyer_name, phone, address, waybill_id,
                    carrier, merchant_notes, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, order_id) DO UPDATE SET
                    status=excluded.status,
                    status_text=excluded.status_text,
                    update_time=excluded.update_time,
                    pay_time=excluded.pay_time,
                    total_amount=excluded.total_amount,
                    product_amount=excluded.product_amount,
                    freight_amount=excluded.freight_amount,
                    discount_amount=excluded.discount_amount,
                    buyer_name=excluded.buyer_name,
                    phone=excluded.phone,
                    address=excluded.address,
                    waybill_id=excluded.waybill_id,
                    carrier=excluded.carrier,
                    merchant_notes=excluded.merchant_notes,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    order.store_id,
                    order.order_id,
                    order.status,
                    order.status_text,
                    order.create_time,
                    order.update_time,
                    order.pay_time,
                    order.total_amount,
                    order.product_amount,
                    order.freight_amount,
                    order.discount_amount,
                    order.buyer_name,
                    order.phone,
                    order.address,
                    order.waybill_id,
                    order.carrier,
                    order.merchant_notes,
                    json.dumps(order.raw, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                "DELETE FROM order_items WHERE store_id=? AND order_id=?",
                (order.store_id, order.order_id),
            )
            conn.executemany(
                """
                INSERT INTO order_items
                    (store_id, order_id, product_id, sku_id, title, sku_count,
                     sale_price, thumb_url, sku_attributes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        order.store_id,
                        order.order_id,
                        item.product_id,
                        item.sku_id,
                        item.title,
                        item.sku_count,
                        item.sale_price,
                        item.thumb_url,
                        item.sku_attributes,
                    )
                    for item in order.items
                ],
            )

    def upsert_product(self, product: Product) -> None:
        self.upsert_products((product,))

    def upsert_products(self, products: tuple[Product, ...] | list[Product]) -> None:
        with self.connect() as conn:
            self._upsert_products(conn, products)

    @staticmethod
    def _upsert_products(
        conn: sqlite3.Connection, products: tuple[Product, ...] | list[Product]
    ) -> None:
        conn.executemany(
            """
            INSERT INTO products (
                store_id, product_id, title, status, status_text, edit_status,
                min_price, sku_count, stock_total, spu_code, out_product_id,
                head_img, raw_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id, product_id) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                status_text=excluded.status_text,
                edit_status=excluded.edit_status,
                min_price=excluded.min_price,
                sku_count=excluded.sku_count,
                stock_total=excluded.stock_total,
                spu_code=excluded.spu_code,
                out_product_id=excluded.out_product_id,
                head_img=excluded.head_img,
                raw_json=excluded.raw_json,
                synced_at=excluded.synced_at
            """,
            (
                (
                    product.store_id,
                    product.product_id,
                    product.title,
                    product.status,
                    product.status_text,
                    product.edit_status,
                    product.min_price,
                    product.sku_count,
                    product.stock_total,
                    product.spu_code,
                    product.out_product_id,
                    product.head_img,
                    json.dumps(product.raw, ensure_ascii=False),
                    _now(),
                )
                for product in products
            ),
        )

    def replace_product_tab(self, store_id: int, tab_key: str, products: list[Product]) -> None:
        if any(product.store_id != store_id for product in products):
            raise ValueError("商品快照包含其他店铺的数据")
        received_ids = {product.product_id for product in products}
        with self.connect() as conn:
            previous = conn.execute(
                "SELECT * FROM products WHERE store_id=?", (store_id,)
            ).fetchall()
            removed = [
                (store_id, row["product_id"])
                for row in previous
                if row["product_id"] not in received_ids
                and product_matches_tab(dict(row), tab_key)
            ]
            self._upsert_products(conn, products)
            conn.executemany(
                "DELETE FROM products WHERE store_id=? AND product_id=?", removed
            )
            conn.executemany(
                "UPDATE product_catalog SET state='pending' WHERE store_id=? AND product_id=?",
                removed,
            )
            conn.execute(
                """
                INSERT INTO product_status_snapshot (
                    store_id, tab_key, product_count, updated_at
                )
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(store_id, tab_key) DO UPDATE SET
                   product_count=excluded.product_count,
                   updated_at=excluded.updated_at
                """,
                (store_id, tab_key, len(received_ids), _now()),
            )

    def list_orders(
        self,
        store_id: int | None = None,
        status: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        search: str = "",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT o.*, s.name AS store_name,
                   GROUP_CONCAT(oi.title || ' ×' || oi.sku_count, '；') AS item_summary
            FROM orders o
            JOIN stores s ON s.id = o.store_id
            LEFT JOIN order_items oi
                ON oi.store_id = o.store_id AND oi.order_id = o.order_id
            WHERE 1=1
        """
        args: list[Any] = []
        if store_id is not None:
            sql += " AND o.store_id = ?"
            args.append(store_id)
        if status is not None:
            sql += " AND o.status = ?"
            args.append(status)
        if start_time is not None:
            sql += " AND o.create_time >= ?"
            args.append(start_time)
        if end_time is not None:
            sql += " AND o.create_time <= ?"
            args.append(end_time)
        if search:
            term = f"%{search.strip()}%"
            sql += " AND (o.order_id LIKE ? OR o.buyer_name LIKE ? OR o.phone LIKE ?)"
            args.extend((term, term, term))
        sql += " GROUP BY o.store_id, o.order_id ORDER BY o.create_time DESC LIMIT ?"
        args.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def get_order_workbench(
        self,
        store_id: int | None = None,
    ) -> dict[tuple[int, str], dict[str, Any]]:
        sql = "SELECT * FROM order_workbench"
        args: tuple[Any, ...] = ()
        if store_id is not None:
            sql += " WHERE store_id=?"
            args = (store_id,)
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return {
            (int(row["store_id"]), str(row["order_id"])): dict(row)
            for row in rows
        }

    def get_order_workbench_row(
        self,
        store_id: int,
        order_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM order_workbench WHERE store_id=? AND order_id=?",
                (store_id, str(order_id)),
            ).fetchone()
        return dict(row) if row else None

    def update_order_workbench(
        self,
        store_id: int,
        order_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        allowed = {
            "purchase_order_no",
            "pickup_code",
            "note",
            "profit",
            "cost",
            "is_marked",
            "is_marked_sent",
            "parsed_at",
            "fulfillment_stage",
            "purchased_at",
            "shipped_at",
            "completed_at",
            "last_error",
            "flag_color",
            "ignored",
        }
        current = self.get_order_workbench_row(store_id, order_id) or {
            "purchase_order_no": "",
            "pickup_code": "",
            "note": "",
            "profit": 0,
            "cost": 0,
            "is_marked": 0,
            "is_marked_sent": 0,
            "parsed_at": "",
            "fulfillment_stage": "",
            "purchased_at": "",
            "shipped_at": "",
            "completed_at": "",
            "last_error": "",
            "flag_color": "",
            "ignored": 0,
        }
        for key, value in changes.items():
            if key in allowed:
                current[key] = value
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO order_workbench (
                    store_id, order_id, purchase_order_no, pickup_code, note,
                    profit, cost, is_marked, is_marked_sent, parsed_at,
                    fulfillment_stage, purchased_at, shipped_at, completed_at,
                    last_error, flag_color, ignored, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(store_id, order_id) DO UPDATE SET
                    purchase_order_no=excluded.purchase_order_no,
                    pickup_code=excluded.pickup_code,
                    note=excluded.note,
                    profit=excluded.profit,
                    cost=excluded.cost,
                    is_marked=excluded.is_marked,
                    is_marked_sent=excluded.is_marked_sent,
                    parsed_at=excluded.parsed_at,
                    fulfillment_stage=excluded.fulfillment_stage,
                    purchased_at=excluded.purchased_at,
                    shipped_at=excluded.shipped_at,
                    completed_at=excluded.completed_at,
                    last_error=excluded.last_error,
                    flag_color=excluded.flag_color,
                    ignored=excluded.ignored,
                    updated_at=excluded.updated_at
                """,
                (
                    store_id,
                    str(order_id),
                    str(current.get("purchase_order_no") or ""),
                    str(current.get("pickup_code") or ""),
                    str(current.get("note") or ""),
                    int(current.get("profit") or 0),
                    int(current.get("cost") or 0),
                    int(current.get("is_marked") or 0),
                    int(current.get("is_marked_sent") or 0),
                    str(current.get("parsed_at") or ""),
                    str(current.get("fulfillment_stage") or ""),
                    str(current.get("purchased_at") or ""),
                    str(current.get("shipped_at") or ""),
                    str(current.get("completed_at") or ""),
                    str(current.get("last_error") or ""),
                    str(current.get("flag_color") or ""),
                    int(current.get("ignored") or 0),
                    _now(),
                ),
            )
        return current

    def apply_remote_order_workbench(
        self,
        store_id: int,
        order_id: str,
        payload: dict[str, Any],
    ) -> None:
        allowed = {
            "purchase_order_no",
            "pickup_code",
            "note",
            "profit",
            "cost",
            "is_marked",
            "is_marked_sent",
            "parsed_at",
            "fulfillment_stage",
            "purchased_at",
            "shipped_at",
            "completed_at",
            "last_error",
            "flag_color",
            "ignored",
        }
        changes = {
            key: value
            for key, value in payload.items()
            if key in allowed
        }
        if changes:
            self.update_order_workbench(
                store_id,
                order_id,
                **changes,
            )

    def delete_order_workbench_row(
        self,
        store_id: int,
        order_id: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM order_workbench WHERE store_id=? AND order_id=?",
                (store_id, str(order_id)),
            )

    def add_procurement_account(
        self,
        platform: str,
        label: str,
        login_id: str = "",
        profile_dir: str = "",
        *,
        nickname: str = "",
        group_name: str = "",
        remark: str = "",
        auto_login: bool = False,
        account_type: int = 2,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO procurement_accounts
                    (platform, label, nickname, group_name, remark, login_id,
                     profile_dir, status, auto_login, account_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, '未登录', ?, ?, ?)
                """,
                (
                    platform,
                    label,
                    nickname or label,
                    group_name,
                    remark,
                    login_id,
                    profile_dir,
                    1 if auto_login else 0,
                    int(account_type),
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def update_procurement_account(
        self,
        account_id: int,
        **changes: Any,
    ) -> None:
        allowed = {
            "label",
            "nickname",
            "group_name",
            "remark",
            "login_id",
            "profile_dir",
            "status",
            "auto_login",
            "account_type",
            "alipay_uid",
            "last_checked_at",
            "last_login",
            "last_sync",
            "last_error",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE procurement_accounts SET {assignments} WHERE id=?",
                (*updates.values(), account_id),
            )

    def set_procurement_account_secrets(
        self,
        account_id: int,
        *,
        password: str | None = None,
        alipay_password: str | None = None,
    ) -> None:
        current = None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT password_protected, alipay_password_protected
                FROM procurement_account_secrets WHERE account_id=?
                """,
                (int(account_id),),
            ).fetchone()
            current = dict(row) if row else {}
            password_protected = (
                self.protector.protect(password)
                if password
                else current.get("password_protected")
            )
            alipay_password_protected = (
                self.protector.protect(alipay_password)
                if alipay_password
                else current.get("alipay_password_protected")
            )
            conn.execute(
                """
                INSERT INTO procurement_account_secrets (
                    account_id, password_protected,
                    alipay_password_protected, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    password_protected=excluded.password_protected,
                    alipay_password_protected=excluded.alipay_password_protected,
                    updated_at=excluded.updated_at
                """,
                (
                    int(account_id),
                    password_protected,
                    alipay_password_protected,
                    _now(),
                ),
            )

    def get_procurement_account_password(self, account_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT password_protected
                FROM procurement_account_secrets WHERE account_id=?
                """,
                (int(account_id),),
            ).fetchone()
        if row is None or not row["password_protected"]:
            return ""
        return self.protector.unprotect(row["password_protected"])

    def get_procurement_alipay_password(self, account_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT alipay_password_protected
                FROM procurement_account_secrets WHERE account_id=?
                """,
                (int(account_id),),
            ).fetchone()
        if row is None or not row["alipay_password_protected"]:
            return ""
        return self.protector.unprotect(row["alipay_password_protected"])

    def delete_procurement_account(self, account_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM procurement_accounts WHERE id=?",
                (account_id,),
            )

    def list_procurement_accounts(
        self,
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM procurement_accounts"
        args: tuple[Any, ...] = ()
        if platform:
            sql += " WHERE platform=?"
            args = (platform,)
        sql += " ORDER BY id"
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def get_procurement_account(self, account_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM procurement_accounts WHERE id=?",
                (account_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_procurement_session(
        self,
        account_id: int,
        payload: dict[str, Any],
        status: str,
        detail: str = "",
    ) -> None:
        protected = self.protector.protect(
            json.dumps(payload, ensure_ascii=False)
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO procurement_sessions
                    (account_id, payload_protected, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    payload_protected=excluded.payload_protected,
                    updated_at=excluded.updated_at
                """,
                (account_id, protected, _now()),
            )
            conn.execute(
                """
                UPDATE procurement_accounts
                SET status=?, last_login=?, last_error=?
                WHERE id=?
                """,
                (status, _now(), detail, account_id),
            )

    def get_procurement_session(
        self,
        account_id: int,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_protected FROM procurement_sessions WHERE account_id=?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        raw = self.protector.unprotect(row["payload_protected"])
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None

    def set_procurement_alipay_session(
        self,
        account_id: int,
        payload: dict[str, Any],
        alipay_uid: str = "",
    ) -> None:
        protected = self.protector.protect(
            json.dumps(payload, ensure_ascii=False)
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO procurement_alipay_sessions
                    (account_id, payload_protected, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    payload_protected=excluded.payload_protected,
                    updated_at=excluded.updated_at
                """,
                (int(account_id), protected, _now()),
            )
            if alipay_uid:
                conn.execute(
                    """
                    UPDATE procurement_accounts
                    SET alipay_uid=?, last_checked_at=?
                    WHERE id=?
                    """,
                    (alipay_uid, _now(), int(account_id)),
                )

    def get_procurement_alipay_session(
        self,
        account_id: int,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_protected
                FROM procurement_alipay_sessions WHERE account_id=?
                """,
                (int(account_id),),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(self.protector.unprotect(row["payload_protected"]))
        return payload if isinstance(payload, dict) else None

    def link_store_procurement_account(
        self,
        store_id: int,
        account_id: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO store_procurement_accounts
                    (store_id, account_id)
                VALUES (?, ?)
                """,
                (store_id, account_id),
            )

    def list_store_procurement_accounts(
        self,
        store_id: int,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.* FROM procurement_accounts a
                JOIN store_procurement_accounts m ON m.account_id=a.id
                WHERE m.store_id=? ORDER BY a.id
                """,
                (store_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_procurement_order(
        self,
        account_id: int,
        platform: str,
        record: dict[str, Any],
    ) -> int:
        now = _now()
        external_order_id = str(
            record.get("external_order_id")
            or record.get("trade_no")
            or record.get("mail_no")
            or ""
        )
        if not external_order_id:
            raise ValueError("采购记录缺少平台订单号")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO procurement_orders (
                    account_id, platform, external_order_id, trade_no, mail_no,
                    pickup_code, station_name, station_phone, status, status_text,
                    goods_title, goods_image, goods_count, pay_amount, seller,
                    buyer, raw_json, fetched_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, external_order_id) DO UPDATE SET
                    trade_no=excluded.trade_no,
                    mail_no=excluded.mail_no,
                    pickup_code=CASE WHEN excluded.pickup_code<>''
                        THEN excluded.pickup_code ELSE procurement_orders.pickup_code END,
                    station_name=CASE WHEN excluded.station_name<>''
                        THEN excluded.station_name ELSE procurement_orders.station_name END,
                    station_phone=CASE WHEN excluded.station_phone<>''
                        THEN excluded.station_phone ELSE procurement_orders.station_phone END,
                    status=excluded.status,
                    status_text=excluded.status_text,
                    goods_title=excluded.goods_title,
                    goods_image=excluded.goods_image,
                    goods_count=excluded.goods_count,
                    pay_amount=excluded.pay_amount,
                    seller=excluded.seller,
                    buyer=excluded.buyer,
                    raw_json=excluded.raw_json,
                    fetched_at=excluded.fetched_at,
                    updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    platform,
                    external_order_id,
                    str(record.get("trade_no") or ""),
                    str(record.get("mail_no") or ""),
                    str(record.get("pickup_code") or ""),
                    str(record.get("station_name") or ""),
                    str(record.get("station_phone") or ""),
                    str(record.get("status") or ""),
                    str(record.get("status_text") or ""),
                    str(record.get("goods_title") or ""),
                    str(record.get("goods_image") or ""),
                    int(record.get("goods_count") or 0),
                    int(record.get("pay_amount") or 0),
                    str(record.get("seller") or ""),
                    str(record.get("buyer") or ""),
                    json.dumps(record.get("raw") or record, ensure_ascii=False),
                    str(record.get("fetched_at") or now),
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM procurement_orders
                WHERE account_id=? AND external_order_id=?
                """,
                (account_id, external_order_id),
            ).fetchone()
        return int(row["id"])

    def list_procurement_orders(
        self,
        account_id: int | None = None,
        platform: str | None = None,
        search: str = "",
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT p.*, a.label AS account_label
            FROM procurement_orders p
            JOIN procurement_accounts a ON a.id=p.account_id
            WHERE 1=1
        """
        args: list[Any] = []
        if account_id is not None:
            sql += " AND p.account_id=?"
            args.append(account_id)
        if platform:
            sql += " AND p.platform=?"
            args.append(platform)
        if search:
            term = f"%{search.strip()}%"
            sql += """
                AND (p.external_order_id LIKE ? OR p.trade_no LIKE ?
                     OR p.mail_no LIKE ? OR p.pickup_code LIKE ?
                     OR p.goods_title LIKE ?)
            """
            args.extend((term, term, term, term, term))
        sql += " ORDER BY p.updated_at DESC, p.id DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def get_procurement_order(self, procurement_order_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, a.label AS account_label
                FROM procurement_orders p
                JOIN procurement_accounts a ON a.id=p.account_id
                WHERE p.id=?
                """,
                (procurement_order_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_procurement_order_local(
        self,
        procurement_order_id: int,
        **changes: Any,
    ) -> None:
        allowed = {
            "trade_no",
            "mail_no",
            "pickup_code",
            "station_name",
            "station_phone",
            "status",
            "status_text",
            "goods_title",
            "goods_count",
            "pay_amount",
            "seller",
            "buyer",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE procurement_orders SET {assignments}, updated_at=? WHERE id=?",
                (*updates.values(), _now(), procurement_order_id),
            )

    def upsert_procurement_link(
        self,
        store_id: int,
        order_id: str,
        procurement_order_id: int,
        match_type: str = "manual",
        confidence: int = 100,
        local_note: str = "",
        sync_wechat_note: bool = False,
    ) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO procurement_links (
                    store_id, order_id, procurement_order_id, match_type,
                    confidence, local_note, sync_wechat_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    store_id, order_id, procurement_order_id
                ) DO UPDATE SET
                    store_id=excluded.store_id,
                    match_type=excluded.match_type,
                    confidence=excluded.confidence,
                    local_note=excluded.local_note,
                    sync_wechat_note=excluded.sync_wechat_note,
                    updated_at=excluded.updated_at
                """,
                (
                    store_id,
                    str(order_id),
                    int(procurement_order_id),
                    match_type,
                    int(confidence),
                    local_note,
                    1 if sync_wechat_note else 0,
                    now,
                    now,
                ),
            )

    def delete_procurement_link(self, link_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM procurement_links WHERE id=?", (link_id,))

    def list_procurement_links(
        self,
        store_id: int | None = None,
        order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT l.*, p.platform, p.external_order_id, p.mail_no, p.pickup_code,
                   p.goods_title, p.pay_amount, p.seller
            FROM procurement_links l
            JOIN procurement_orders p ON p.id=l.procurement_order_id
            WHERE 1=1
        """
        args: list[Any] = []
        if store_id is not None:
            sql += " AND l.store_id=?"
            args.append(store_id)
        if order_id is not None:
            sql += " AND l.order_id=?"
            args.append(str(order_id))
        sql += " ORDER BY l.updated_at DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def mark_procurement_note_synced(self, link_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE procurement_links
                SET wechat_note_synced_at=?, updated_at=?
                WHERE id=?
                """,
                (_now(), _now(), link_id),
            )

    def get_order(self, store_id: int, order_id: str) -> Order | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT o.*, s.name AS store_name FROM orders o
                JOIN stores s ON s.id=o.store_id
                WHERE o.store_id=? AND o.order_id=?
                """,
                (store_id, order_id),
            ).fetchone()
            if row is None:
                return None
            item_rows = conn.execute(
                "SELECT * FROM order_items WHERE store_id=? AND order_id=?",
                (store_id, order_id),
            ).fetchall()
        items = [
            OrderItem(
                product_id=item["product_id"],
                sku_id=item["sku_id"],
                title=item["title"],
                sku_count=item["sku_count"],
                sale_price=item["sale_price"],
                thumb_url=item["thumb_url"],
                sku_attributes=item["sku_attributes"],
            )
            for item in item_rows
        ]
        return Order(
            store_id=row["store_id"],
            store_name=row["store_name"],
            order_id=row["order_id"],
            status=row["status"],
            status_text=row["status_text"],
            create_time=row["create_time"],
            update_time=row["update_time"],
            pay_time=row["pay_time"],
            total_amount=row["total_amount"],
            product_amount=row["product_amount"],
            freight_amount=row["freight_amount"],
            discount_amount=row["discount_amount"],
            buyer_name=row["buyer_name"],
            phone=row["phone"],
            address=row["address"],
            waybill_id=row["waybill_id"],
            carrier=row["carrier"],
            merchant_notes=row["merchant_notes"],
            items=items,
            raw=json.loads(row["raw_json"] or "{}"),
        )

    def list_products(
        self,
        store_id: int | None = None,
        status: int | None = None,
        search: str = "",
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT p.*, s.name AS store_name FROM products p
            JOIN stores s ON s.id=p.store_id WHERE 1=1
        """
        args: list[Any] = []
        if store_id is not None:
            sql += " AND p.store_id=?"
            args.append(store_id)
        if status is not None:
            sql += " AND p.status=?"
            args.append(status)
        if search:
            term = f"%{search.strip()}%"
            sql += " AND (p.title LIKE ? OR p.product_id LIKE ? OR p.spu_code LIKE ?)"
            args.extend((term, term, term))
        sql += " ORDER BY p.synced_at DESC, p.product_id DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def get_product(self, store_id: int, product_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE store_id=? AND product_id=?",
                (store_id, product_id),
            ).fetchone()
        return dict(row) if row else None

    def product_catalog_stats(self, store_id: int | None = None) -> dict[str, int]:
        sql = """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN state='done' THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN state='pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN state='missing' THEN 1 ELSE 0 END) AS missing
            FROM product_catalog
        """
        args: tuple[Any, ...] = ()
        if store_id is not None:
            sql += " WHERE store_id=?"
            args = (store_id,)
        with self.connect() as conn:
            row = conn.execute(sql, args).fetchone()
        return {
            "total": int(row["total"] or 0),
            "done": int(row["done"] or 0),
            "pending": int(row["pending"] or 0),
            "failed": int(row["failed"] or 0),
            "missing": int(row["missing"] or 0),
        }

    def store_summary(self, store_id: int) -> dict[str, int]:
        now = datetime.now()
        today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        week_start = int(
            (now - timedelta(days=6))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        month_start = int(
            (now - timedelta(days=29))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        with self.connect() as conn:
            in_sale = conn.execute(
                "SELECT COUNT(*) FROM products WHERE store_id=? AND status=5",
                (store_id,),
            ).fetchone()[0]
            warehouse = conn.execute(
                "SELECT COUNT(*) FROM product_catalog WHERE store_id=?",
                (store_id,),
            ).fetchone()[0]
            today_revenue, today_count = conn.execute(
                """
                SELECT COALESCE(SUM(total_amount), 0), COUNT(*)
                FROM orders
                WHERE store_id=? AND create_time>=?
                  AND status IN (17, 20, 21, 30, 100)
                """,
                (store_id, today_start),
            ).fetchone()
            week_revenue = conn.execute(
                """
                SELECT COALESCE(SUM(total_amount), 0)
                FROM orders
                WHERE store_id=? AND create_time>=?
                  AND status IN (17, 20, 21, 30, 100)
                """,
                (store_id, week_start),
            ).fetchone()[0]
            month_revenue = conn.execute(
                """
                SELECT COALESCE(SUM(total_amount), 0)
                FROM orders
                WHERE store_id=? AND create_time>=?
                  AND status IN (17, 20, 21, 30, 100)
                """,
                (store_id, month_start),
            ).fetchone()[0]
        return {
            "in_sale": int(in_sale or 0),
            "warehouse": int(warehouse or 0),
            "today_revenue": int(today_revenue or 0),
            "today_orders": int(today_count or 0),
            "week_revenue": int(week_revenue or 0),
            "month_revenue": int(month_revenue or 0),
        }

    def clear_store_cache(self, store_id: int) -> None:
        with self.connect() as conn:
            for table in (
                "hosted_runs",
                "analytics_daily",
                "after_sales",
                "orders",
                "product_catalog",
                "product_metrics",
                "product_status_snapshot",
                "product_scan_state",
                "product_operations",
                "products",
            ):
                conn.execute(f"DELETE FROM {table} WHERE store_id=?", (store_id,))

    def upsert_product_status_snapshot(
        self,
        store_id: int,
        counts: dict[str, int],
    ) -> None:
        now = _now()
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO product_status_snapshot
                    (store_id, tab_key, product_count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(store_id, tab_key) DO UPDATE SET
                    product_count=excluded.product_count,
                    updated_at=excluded.updated_at
                """,
                [
                    (store_id, key, int(count), now)
                    for key, count in counts.items()
                ],
            )

    def get_product_status_snapshot(
        self,
        store_id: int | None = None,
    ) -> dict[str, int]:
        if store_id is None:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT tab_key, SUM(product_count) AS product_count
                    FROM product_status_snapshot
                    GROUP BY tab_key
                    """
                ).fetchall()
        else:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT tab_key, product_count
                    FROM product_status_snapshot
                    WHERE store_id=?
                    """,
                    (store_id,),
                ).fetchall()
        return {
            str(row["tab_key"]): int(row["product_count"] or 0)
            for row in rows
        }

    def upsert_product_metrics(
        self,
        store_id: int,
        product_id: str,
        **metrics: int,
    ) -> None:
        allowed = {
            "views30",
            "sold30",
            "click_cnt",
            "click_uv",
            "pay_cnt",
            "pay_uv",
            "pay_gmv",
        }
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM product_metrics
                WHERE store_id=? AND product_id=?
                """,
                (store_id, str(product_id)),
            ).fetchone()
        current = dict(row) if row else {}
        for key, value in metrics.items():
            if key in allowed:
                current[key] = int(value or 0)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO product_metrics (
                    store_id, product_id, views30, sold30, click_cnt, click_uv,
                    pay_cnt, pay_uv, pay_gmv, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, product_id) DO UPDATE SET
                    views30=excluded.views30,
                    sold30=excluded.sold30,
                    click_cnt=excluded.click_cnt,
                    click_uv=excluded.click_uv,
                    pay_cnt=excluded.pay_cnt,
                    pay_uv=excluded.pay_uv,
                    pay_gmv=excluded.pay_gmv,
                    updated_at=excluded.updated_at
                """,
                (
                    store_id,
                    str(product_id),
                    int(current.get("views30") or 0),
                    int(current.get("sold30") or 0),
                    int(current.get("click_cnt") or 0),
                    int(current.get("click_uv") or 0),
                    int(current.get("pay_cnt") or 0),
                    int(current.get("pay_uv") or 0),
                    int(current.get("pay_gmv") or 0),
                    _now(),
                ),
            )

    def get_product_metrics_map(
        self,
        store_id: int,
    ) -> dict[str, dict[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM product_metrics WHERE store_id=?",
                (store_id,),
            ).fetchall()
        return {
            str(row["product_id"]): {
                "views30": int(row["views30"] or 0),
                "sold30": int(row["sold30"] or 0),
                "click_cnt": int(row["click_cnt"] or 0),
                "click_uv": int(row["click_uv"] or 0),
                "pay_cnt": int(row["pay_cnt"] or 0),
                "pay_uv": int(row["pay_uv"] or 0),
                "pay_gmv": int(row["pay_gmv"] or 0),
            }
            for row in rows
        }

    def get_hosted_config(self, store_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM hosted_configs WHERE store_id=?",
                (store_id,),
            ).fetchone()
            extra_row = conn.execute(
                "SELECT value FROM app_settings WHERE key=?",
                (f"hosted_config_extra_{store_id}",),
            ).fetchone()
        if not row and not extra_row:
            return None
        result = dict(row) if row else {"store_id": store_id}
        if extra_row:
            try:
                extra = json.loads(extra_row["value"])
            except (TypeError, ValueError):
                extra = {}
            if isinstance(extra, dict):
                result.update(extra)
        return result

    def update_hosted_config(self, store_id: int, **changes: Any) -> dict[str, Any]:
        boolean_fields = {
            "revoke_audit",
            "delete_audit_failed",
            "list_products",
            "prefill_11",
            "enabled",
        }
        current = self.get_hosted_config(store_id) or {
            "store_id": store_id,
            "revoke_audit": 0,
            "delete_audit_failed": 0,
            "list_products": 0,
            "prefill_11": 0,
            "enabled": 0,
        }
        extra_changes: dict[str, Any] = {}
        for key, value in changes.items():
            if key in boolean_fields:
                current[key] = 1 if value else 0
            else:
                extra_changes[key] = value
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO hosted_configs (
                    store_id, revoke_audit, delete_audit_failed,
                    list_products, prefill_11, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                    revoke_audit=excluded.revoke_audit,
                    delete_audit_failed=excluded.delete_audit_failed,
                    list_products=excluded.list_products,
                    prefill_11=excluded.prefill_11,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    store_id,
                    int(current.get("revoke_audit") or 0),
                    int(current.get("delete_audit_failed") or 0),
                    int(current.get("list_products") or 0),
                    int(current.get("prefill_11") or 0),
                    int(current.get("enabled") or 0),
                    _now(),
                ),
            )
            if extra_changes:
                extra_row = conn.execute(
                    "SELECT value FROM app_settings WHERE key=?",
                    (f"hosted_config_extra_{store_id}",),
                ).fetchone()
                try:
                    extra = json.loads(
                        extra_row["value"] if extra_row else "{}"
                    )
                except (TypeError, ValueError):
                    extra = {}
                if not isinstance(extra, dict):
                    extra = {}
                extra.update(extra_changes)
                conn.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (
                        f"hosted_config_extra_{store_id}",
                        json.dumps(extra, ensure_ascii=False),
                        _now(),
                    ),
                )
                current.update(extra_changes)
        return current

    def log_hosted_run(
        self,
        store_id: int,
        actions: str,
        result: str,
        message: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO hosted_runs
                    (store_id, actions, result, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (store_id, actions, result, message[:2000], _now()),
            )

    def list_hosted_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, s.name AS store_name
                FROM hosted_runs r
                JOIN stores s ON s.id=r.store_id
                ORDER BY r.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_automation_task(
        self,
        name: str,
        task_type: str,
        store_id: int | None,
        interval_minutes: int,
    ) -> int:
        now = _now()
        next_run = (
            datetime.now() + timedelta(minutes=max(1, int(interval_minutes)))
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO automation_tasks (
                    name, task_type, store_id, interval_minutes, enabled,
                    next_run, created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    name,
                    task_type,
                    store_id,
                    max(1, int(interval_minutes)),
                    next_run,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_automation_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*, s.name AS store_name
                FROM automation_tasks t
                LEFT JOIN stores s ON s.id=t.store_id
                ORDER BY t.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def update_automation_task(
        self,
        task_id: int,
        **changes: Any,
    ) -> None:
        allowed = {
            "name",
            "task_type",
            "store_id",
            "interval_minutes",
            "enabled",
            "last_run",
            "next_run",
            "last_status",
            "last_message",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE automation_tasks SET {assignments} WHERE id=?",
                (*updates.values(), task_id),
            )

    def delete_automation_task(self, task_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM automation_tasks WHERE id=?", (task_id,))

    def log_automation_run(
        self,
        task_id: int,
        status: str,
        message: str,
    ) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO automation_runs
                    (task_id, started_at, finished_at, status, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, now, now, status, message[:2000]),
            )

    def list_automation_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, t.name AS task_name
                FROM automation_runs r
                JOIN automation_tasks t ON t.id=r.task_id
                ORDER BY r.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_product_scan_state(self, store_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM product_scan_state WHERE store_id=?",
                (store_id,),
            ).fetchone()
        return dict(row) if row else None

    def begin_product_scan(
        self,
        store_id: int,
        scan_id: str,
        reset: bool = False,
    ) -> dict[str, Any]:
        now = _now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM product_scan_state WHERE store_id=?",
                (store_id,),
            ).fetchone()
            if row and row["status"] == "running" and not reset:
                return dict(row)
            conn.execute(
                """
                INSERT INTO product_scan_state (
                    store_id, scan_id, next_key, status, discovered,
                    started_at, updated_at, completed_at
                )
                VALUES (?, ?, '', 'running', 0, ?, ?, NULL)
                ON CONFLICT(store_id) DO UPDATE SET
                    scan_id=excluded.scan_id,
                    next_key='',
                    status='running',
                    discovered=0,
                    started_at=excluded.started_at,
                    updated_at=excluded.updated_at,
                    completed_at=NULL
                """,
                (store_id, scan_id, now, now),
            )
        return {
            "store_id": store_id,
            "scan_id": scan_id,
            "next_key": "",
            "status": "running",
            "discovered": 0,
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
        }

    def save_product_scan_page(
        self,
        store_id: int,
        scan_id: str,
        product_ids: list[str],
        next_key: str,
    ) -> int:
        now = _now()
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO product_catalog (
                    store_id, product_id, state, attempts, last_error,
                    first_seen_at, last_seen_at, detail_synced_at, last_scan_id
                )
                VALUES (?, ?, 'pending', 0, '', ?, ?, NULL, ?)
                ON CONFLICT(store_id, product_id) DO UPDATE SET
                    state=CASE
                        WHEN product_catalog.state='done' THEN 'done'
                        ELSE 'pending'
                    END,
                    last_error='',
                    last_seen_at=excluded.last_seen_at,
                    last_scan_id=excluded.last_scan_id
                """,
                [(store_id, product_id, now, now, scan_id) for product_id in product_ids],
            )
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM product_catalog WHERE store_id=? AND last_scan_id=?",
                (store_id, scan_id),
            ).fetchone()
            conn.execute(
                """
                UPDATE product_scan_state
                SET next_key=?, discovered=?, updated_at=?
                WHERE store_id=? AND scan_id=?
                """,
                (next_key, int(row["count"] or 0), now, store_id, scan_id),
            )
            return int(row["count"] or 0)

    def complete_product_scan(self, store_id: int, scan_id: str) -> dict[str, int]:
        now = _now()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE product_catalog
                SET state='missing'
                WHERE store_id=? AND last_scan_id<>? AND state<>'missing'
                """,
                (store_id, scan_id),
            )
            conn.execute(
                """
                UPDATE product_scan_state
                SET status='completed', next_key='', completed_at=?, updated_at=?
                WHERE store_id=? AND scan_id=?
                """,
                (now, now, store_id, scan_id),
            )
        return {"marked_missing": int(cursor.rowcount or 0)}

    def list_pending_product_ids(
        self,
        store_id: int,
        limit: int | None = None,
        include_failed: bool = True,
        include_missing: bool = False,
    ) -> list[str]:
        states = ["pending"]
        if include_failed:
            states.append("failed")
        if include_missing:
            states.append("missing")
        placeholders = ",".join("?" for _ in states)
        sql = (
            "SELECT product_id FROM product_catalog "
            f"WHERE store_id=? AND state IN ({placeholders}) "
            "ORDER BY first_seen_at, product_id"
        )
        args: list[Any] = [store_id, *states]
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            args.append(limit)
        with self.connect() as conn:
            return [str(row["product_id"]) for row in conn.execute(sql, args).fetchall()]

    def mark_product_catalog_done(self, store_id: int, product_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE product_catalog
                SET state='done', attempts=attempts+1, last_error='', detail_synced_at=?
                WHERE store_id=? AND product_id=?
                """,
                (_now(), store_id, product_id),
            )

    def mark_product_catalog_failed(self, store_id: int, product_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE product_catalog
                SET state='failed', attempts=attempts+1, last_error=?
                WHERE store_id=? AND product_id=?
                """,
                (error[:1000], store_id, product_id),
            )

    def mark_product_status(
        self,
        store_id: int,
        product_id: str,
        status: int,
        status_text: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE products
                SET status=?, status_text=?, synced_at=?
                WHERE store_id=? AND product_id=?
                """,
                (status, status_text, _now(), store_id, product_id),
            )

    def remove_product_cache(self, store_id: int, product_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM products WHERE store_id=? AND product_id=?",
                (store_id, product_id),
            )
            conn.execute(
                "UPDATE product_catalog SET state='missing' WHERE store_id=? AND product_id=?",
                (store_id, product_id),
            )

    def set_product_stock_cache(
        self,
        store_id: int,
        product_id: str,
        sku_id: str,
        new_stock: int,
    ) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM products WHERE store_id=? AND product_id=?",
                (store_id, product_id),
            ).fetchone()
            if row is None:
                return
            payload = json.loads(row["raw_json"] or "{}")
            skus = payload.get("skus") or []
            found = False
            for sku in skus:
                if str(sku.get("sku_id")) == str(sku_id):
                    sku["stock_num"] = max(0, int(new_stock))
                    found = True
                    break
            if not found:
                return
            stock_total = sum(int(sku.get("stock_num") or 0) for sku in skus)
            conn.execute(
                """
                UPDATE products
                SET stock_total=?, raw_json=?, synced_at=?
                WHERE store_id=? AND product_id=?
                """,
                (
                    stock_total,
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                    store_id,
                    product_id,
                ),
            )

    def log_product_operation(
        self,
        store_id: int,
        product_id: str,
        action: str,
        status: str,
        error: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO product_operations
                    (store_id, product_id, action, status, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (store_id, product_id, action, status, error[:1000], _now()),
            )

    def list_product_operations(
        self,
        store_id: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT product_id, action, status, error, created_at
                FROM product_operations
                WHERE store_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (store_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_shipping_config(
        self,
        store_id: int,
        sender: dict[str, str],
        acct_id: str,
        delivery_id: str,
        template_id: str,
    ) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO shipping_config (
                    store_id, sender_name, sender_mobile, sender_province,
                    sender_city, sender_county, sender_street, sender_address,
                    default_acct_id, default_delivery_id, default_template_id,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                    sender_name=excluded.sender_name,
                    sender_mobile=excluded.sender_mobile,
                    sender_province=excluded.sender_province,
                    sender_city=excluded.sender_city,
                    sender_county=excluded.sender_county,
                    sender_street=excluded.sender_street,
                    sender_address=excluded.sender_address,
                    default_acct_id=excluded.default_acct_id,
                    default_delivery_id=excluded.default_delivery_id,
                    default_template_id=excluded.default_template_id,
                    updated_at=excluded.updated_at
                """,
                (
                    store_id,
                    sender.get("name", ""),
                    sender.get("mobile", ""),
                    sender.get("province", ""),
                    sender.get("city", ""),
                    sender.get("county", ""),
                    sender.get("street", ""),
                    sender.get("address", ""),
                    acct_id,
                    delivery_id,
                    template_id,
                    now,
                ),
            )

    def get_shipping_config(self, store_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shipping_config WHERE store_id=?",
                (store_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_shipping_account(self, account: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO shipping_accounts (
                    store_id, acct_id, delivery_id, delivery_name, shop_id,
                    site_code, site_name, status, available, monthly_card,
                    sender_name, sender_mobile, sender_address_json,
                    raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, acct_id) DO UPDATE SET
                    delivery_id=excluded.delivery_id,
                    delivery_name=excluded.delivery_name,
                    shop_id=excluded.shop_id,
                    site_code=excluded.site_code,
                    site_name=excluded.site_name,
                    status=excluded.status,
                    available=excluded.available,
                    monthly_card=excluded.monthly_card,
                    sender_name=excluded.sender_name,
                    sender_mobile=excluded.sender_mobile,
                    sender_address_json=excluded.sender_address_json,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    account.store_id,
                    account.acct_id,
                    account.delivery_id,
                    account.delivery_name,
                    account.shop_id,
                    account.site_code,
                    account.site_name,
                    account.status,
                    account.available,
                    account.monthly_card,
                    account.sender_name,
                    account.sender_mobile,
                    json.dumps(account.sender_address, ensure_ascii=False),
                    json.dumps(account.raw, ensure_ascii=False),
                    _now(),
                ),
            )

    def upsert_shipping_template(self, template: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO shipping_templates (
                    store_id, delivery_id, template_id, template_name,
                    template_type, is_default, raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, delivery_id, template_id) DO UPDATE SET
                    template_name=excluded.template_name,
                    template_type=excluded.template_type,
                    is_default=excluded.is_default,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    template.store_id,
                    template.delivery_id,
                    template.template_id,
                    template.template_name,
                    template.template_type,
                    1 if template.is_default else 0,
                    json.dumps(template.raw, ensure_ascii=False),
                    _now(),
                ),
            )

    def list_shipping_accounts(self, store_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM shipping_accounts
                WHERE store_id=?
                ORDER BY delivery_name, site_name, acct_id
                """,
                (store_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_shipping_templates(
        self,
        store_id: int,
        delivery_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM shipping_templates WHERE store_id=?"
        args: list[Any] = [store_id]
        if delivery_id:
            sql += " AND delivery_id=?"
            args.append(delivery_id)
        sql += " ORDER BY is_default DESC, delivery_id, template_name"
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def upsert_shipment(
        self,
        store_id: int,
        order_id: str,
        ewaybill_order_id: str,
        delivery_id: str,
        acct_id: str,
        template_id: str,
        waybill_id: str,
        status: str,
        print_info: str = "",
        error: str = "",
    ) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO shipments (
                    store_id, order_id, ewaybill_order_id, delivery_id,
                    acct_id, template_id, waybill_id, status, print_info,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id, order_id) DO UPDATE SET
                    ewaybill_order_id=excluded.ewaybill_order_id,
                    delivery_id=excluded.delivery_id,
                    acct_id=excluded.acct_id,
                    template_id=excluded.template_id,
                    waybill_id=excluded.waybill_id,
                    status=excluded.status,
                    print_info=excluded.print_info,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    store_id,
                    order_id,
                    ewaybill_order_id,
                    delivery_id,
                    acct_id,
                    template_id,
                    waybill_id,
                    status,
                    print_info[:100000],
                    error[:1000],
                    now,
                    now,
                ),
            )

    def get_shipment(self, store_id: int, order_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shipments WHERE store_id=? AND order_id=?",
                (store_id, order_id),
            ).fetchone()
        return dict(row) if row else None

    def update_order_delivery(
        self,
        store_id: int,
        order_id: str,
        waybill_id: str,
        carrier: str,
        status: int,
        status_text: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET waybill_id=?, carrier=?, status=?, status_text=?, synced_at=?
                WHERE store_id=? AND order_id=?
                """,
                (waybill_id, carrier, status, status_text, _now(), store_id, order_id),
            )

    def update_order_note(self, store_id: int, order_id: str, notes: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE orders
                SET merchant_notes=?, synced_at=?
                WHERE store_id=? AND order_id=?
                """,
                (notes, _now(), store_id, order_id),
            )

    def start_sync_run(self, store_id: int | None, resource: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_runs(store_id, resource, started_at, status)
                VALUES (?, ?, ?, 'running')
                """,
                (store_id, resource, _now()),
            )
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        status: str,
        count: int = 0,
        message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET finished_at=?, status=?, item_count=?, message=?
                WHERE id=?
                """,
                (_now(), status, count, message[:1000], run_id),
            )

    def start_task_log(
        self,
        scope: str,
        operation: str,
        message: str = "",
        store_id: int | None = None,
        detail: str = "",
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO task_logs(
                    scope, operation, status, message, detail,
                    started_at, store_id
                )
                VALUES (?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    scope[:80],
                    operation[:160],
                    message[:1000],
                    detail[:12000],
                    _now(),
                    store_id,
                ),
            )
            return int(cursor.lastrowid)

    def finish_task_log(
        self,
        log_id: int,
        status: str,
        message: str = "",
        detail: str = "",
        duration_ms: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE task_logs
                SET status=?, message=?, detail=?, finished_at=?,
                    duration_ms=?
                WHERE id=?
                """,
                (
                    status[:32],
                    message[:1000],
                    detail[:12000],
                    _now(),
                    max(0, int(duration_ms)),
                    log_id,
                ),
            )

    def list_task_logs(
        self,
        limit: int = 500,
        scope: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT l.*, COALESCE(s.name, '') AS store_name
            FROM task_logs l
            LEFT JOIN stores s ON s.id=l.store_id
            WHERE 1=1
        """
        args: list[Any] = []
        if scope:
            sql += " AND l.scope=?"
            args.append(scope)
        if status:
            sql += " AND l.status=?"
            args.append(status)
        sql += " ORDER BY l.started_at DESC, l.id DESC LIMIT ?"
        args.append(max(1, min(5000, int(limit))))
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def clear_task_logs(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM task_logs")

    def record_upload_history(
        self,
        store_id: int,
        source_product_id: str,
        product_id: str = "",
        title: str = "",
    ) -> None:
        source_product_id = str(source_product_id or "").strip()
        if not source_product_id:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO upload_history(
                    store_id, source_product_id, product_id, title, uploaded_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(store_id, source_product_id) DO UPDATE SET
                    product_id=excluded.product_id,
                    title=excluded.title,
                    uploaded_at=excluded.uploaded_at
                """,
                (
                    store_id,
                    source_product_id[:200],
                    str(product_id or "")[:200],
                    str(title or "")[:1000],
                    _now(),
                ),
            )

    def commit_upload_task_result(
        self,
        tasks_json: str,
        store_id: int,
        source_product_id: str,
        product_id: str,
        title: str,
        *,
        source_link: dict[str, Any] | None = None,
        sku_mappings: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist task, history, source link and SKU matches atomically."""
        source_product_id = str(source_product_id or "").strip()
        with self.connect() as conn:
            now = _now()
            conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES ('upload_tasks', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (tasks_json, now),
            )
            if source_product_id:
                conn.execute(
                    """
                    INSERT INTO upload_history(
                        store_id, source_product_id, product_id, title, uploaded_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, source_product_id) DO UPDATE SET
                        product_id=excluded.product_id,
                        title=excluded.title,
                        uploaded_at=excluded.uploaded_at
                    """,
                    (
                        store_id,
                        source_product_id[:200],
                        str(product_id or "")[:200],
                        str(title or "")[:1000],
                        now,
                    ),
                )
            if source_link is None:
                return
            source_url = str(source_link.get("source_url") or "")
            link_source_id = str(
                source_link.get("source_product_id")
                or source_product_id
            )
            existing = conn.execute(
                """
                SELECT id FROM product_purchase_links
                WHERE store_id=? AND product_id=?
                  AND (
                    (? <> '' AND source_url=?)
                    OR (? <> '' AND source_product_id=?)
                  )
                ORDER BY id
                LIMIT 1
                """,
                (
                    int(store_id),
                    str(product_id),
                    source_url,
                    source_url,
                    link_source_id,
                    link_source_id,
                ),
            ).fetchone()
            source_skus = source_link.get("source_skus")
            values = (
                source_url,
                str(source_link.get("platform") or ""),
                1 if source_link.get("use_transfer_warehouse") else 0,
                float(source_link.get("commission_pct") or 0),
                str(source_link.get("note") or ""),
                json.dumps(
                    source_link.get("address_info")
                    if isinstance(source_link.get("address_info"), dict)
                    else {},
                    ensure_ascii=False,
                ),
                link_source_id,
                str(source_link.get("source_title") or title or ""),
                (
                    json.dumps(source_skus, ensure_ascii=False)
                    if isinstance(source_skus, list)
                    else "[]"
                ),
                now,
            )
            if existing is not None:
                link_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE product_purchase_links
                    SET source_url=?, platform=?, use_transfer_warehouse=?,
                        commission_pct=?, note=?, address_info_json=?,
                        source_product_id=?, source_title=?,
                        source_skus_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (*values, link_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO product_purchase_links (
                        store_id, product_id, source_url, platform,
                        use_transfer_warehouse, commission_pct, note,
                        address_info_json, source_product_id, source_title,
                        source_skus_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(store_id),
                        str(product_id),
                        *values[:-1],
                        now,
                        now,
                    ),
                )
                link_id = int(cursor.lastrowid)
            for mapping in sku_mappings or []:
                store_sku_id = str(
                    mapping.get("store_sku_id") or ""
                ).strip()
                if not store_sku_id:
                    continue
                attrs = mapping.get("purchase_attrs")
                conn.execute(
                    """
                    INSERT INTO product_sku_mappings (
                        store_id, product_id, store_sku_id,
                        purchase_link_id, purchase_sku_id,
                        purchase_attrs_json, purchase_price,
                        purchase_image, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(store_id, product_id, store_sku_id)
                    DO UPDATE SET
                        purchase_link_id=excluded.purchase_link_id,
                        purchase_sku_id=excluded.purchase_sku_id,
                        purchase_attrs_json=excluded.purchase_attrs_json,
                        purchase_price=excluded.purchase_price,
                        purchase_image=excluded.purchase_image,
                        updated_at=excluded.updated_at
                    """,
                    (
                        int(store_id),
                        str(product_id),
                        store_sku_id,
                        link_id,
                        str(mapping.get("purchase_sku_id") or ""),
                        json.dumps(
                            attrs if isinstance(attrs, list) else [],
                            ensure_ascii=False,
                        ),
                        int(mapping.get("purchase_price") or 0),
                        str(mapping.get("purchase_image") or ""),
                        now,
                    ),
                )

    def uploaded_source_ids(
        self,
        store_id: int | None = None,
    ) -> set[str]:
        sql = "SELECT DISTINCT source_product_id FROM upload_history"
        args: tuple[Any, ...] = ()
        if store_id is not None:
            sql += " WHERE store_id=?"
            args = (store_id,)
        with self.connect() as conn:
            return {
                str(row["source_product_id"])
                for row in conn.execute(sql, args).fetchall()
                if str(row["source_product_id"] or "").strip()
            }

    def recover_interrupted_task_logs(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_logs
                SET status='failed',
                    message='上次运行中断，任务未正常结束',
                    detail=CASE
                        WHEN detail='' THEN '程序退出或崩溃导致任务中断'
                        ELSE detail || char(10) || '程序退出或崩溃导致任务中断'
                    END,
                    finished_at=?
                WHERE status='running'
                """,
                (_now(),),
            )
            return int(cursor.rowcount)

    @staticmethod
    def _store_from_row(row: sqlite3.Row) -> Store:
        return Store(
            id=row["id"],
            name=row["name"],
            app_id=row["app_id"],
            group_name=row["group_name"],
            status=row["status"],
            last_sync_at=row["last_sync_at"],
            created_at=row["created_at"],
            web_status=row["web_status"] if "web_status" in row.keys() else "未登录",
            web_status_detail=(
                row["web_status_detail"]
                if "web_status_detail" in row.keys()
                else ""
            ),
            web_checked_at=(
                row["web_checked_at"] if "web_checked_at" in row.keys() else None
            ),
            web_logged_in_at=(
                row["web_logged_in_at"]
                if "web_logged_in_at" in row.keys()
                else None
            ),
        )


def order_status_text(status: int) -> str:
    return ORDER_STATUS.get(status, f"未知({status})")


def product_status_text(status: int) -> str:
    return PRODUCT_STATUS.get(status, f"未知({status})")
