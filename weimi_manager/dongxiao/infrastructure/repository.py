from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from ..domain.catalog import CatalogProductData
from ..domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    ApprovalSubjectType,
    AuditResult,
    DataSource,
    OrderStatus,
    OrderVerificationSource,
    PresenceState,
    ProductPoolStatus,
    ProductState,
    TaskEvent,
    TaskItemStatus,
    TaskStatus,
)
from ..domain.imports import ProductImportPreview
from ..domain.models import (
    AftersaleCase,
    Approval,
    AuditEvent,
    OrderRecord,
    OrderStatusSnapshot,
    Product,
    ProductPool,
    ProductPoolEvent,
    ProductPoolItem,
    ProductSnapshot,
    RealOrderReference,
    Sku,
    Task,
    TaskAttempt,
    TaskExecutionItem,
    TaskItem,
)
from ..domain.operations import ApiCallRecord, TaskStatistics
from ..domain.queries import (
    ProductFilter,
    ProductPage,
    ProductSortField,
)
from ..domain.rules import (
    StoreIsolationError,
    ensure_same_store,
    parse_utc_iso,
    sanitize_audit_detail,
    task_request_fingerprint,
    to_utc_iso,
)
from ..domain.state_machine import (
    transition_aftersale,
    transition_approval,
    transition_task,
)


class RepositoryError(RuntimeError):
    pass


class IdempotencyConflict(RepositoryError):
    def __init__(self, existing_id: str) -> None:
        super().__init__(f"幂等键已用于其他请求：{existing_id}")
        self.existing_id = existing_id


class EntityNotFound(RepositoryError):
    pass


def _json(value: object) -> str:
    return json.dumps(
        sanitize_audit_detail(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_list(value: str) -> list[Any]:
    try:
        payload = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def _row_time(value: str | None) -> datetime | None:
    return parse_utc_iso(value) if value else None


class DongxiaoRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path, timeout=15)
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

    @staticmethod
    def _ensure_store(
        conn: sqlite3.Connection,
        store_id: int,
        store_app_id: str,
    ) -> None:
        row = conn.execute(
            "SELECT app_id FROM stores WHERE id=?",
            (int(store_id),),
        ).fetchone()
        if row is None:
            raise EntityNotFound("店铺不存在")
        if str(row["app_id"]) != str(store_app_id):
            raise StoreIsolationError("店铺 ID 与 AppID 不一致")

    def get_store_identity(
        self,
        store_id: int,
        store_app_id: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, app_id
                FROM stores
                WHERE id=?
                """,
                (int(store_id),),
            ).fetchone()
        if row is None:
            raise EntityNotFound("店铺不存在")
        if str(row["app_id"]) != str(store_app_id):
            raise StoreIsolationError("店铺 ID 与 AppID 不一致")
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "app_id": str(row["app_id"]),
        }

    def upsert_product(self, product: Product) -> Product:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(
                conn,
                product.store_id,
                product.store_app_id,
            )
            now = to_utc_iso(datetime.now(UTC))
            conn.execute(
                """
                INSERT INTO dongxiao_products(
                    store_id, store_app_id, product_id, title, state,
                    min_price_cents, stock_total, observed_at,
                    created_at, updated_at, sales_count, sales_source,
                    listing_time, listing_source, data_source,
                    from_cache, last_synced_at, presence_state, missing_since
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'present', NULL
                )
                ON CONFLICT(store_app_id, product_id) DO UPDATE SET
                    store_id=excluded.store_id,
                    title=excluded.title,
                    state=excluded.state,
                    min_price_cents=excluded.min_price_cents,
                    stock_total=excluded.stock_total,
                    observed_at=excluded.observed_at,
                    updated_at=excluded.updated_at,
                    sales_count=excluded.sales_count,
                    sales_source=excluded.sales_source,
                    listing_time=excluded.listing_time,
                    listing_source=excluded.listing_source,
                    data_source=excluded.data_source,
                    from_cache=excluded.from_cache,
                    last_synced_at=excluded.last_synced_at,
                    presence_state='present',
                    missing_since=NULL
                """,
                (
                    product.store_id,
                    product.store_app_id,
                    product.product_id,
                    product.title,
                    product.state.value,
                    product.min_price_cents,
                    product.stock_total,
                    to_utc_iso(product.observed_at),
                    now,
                    now,
                    product.sales_count,
                    (
                        product.sales_source.value
                        if product.sales_source is not None
                        else None
                    ),
                    (
                        to_utc_iso(product.listing_time)
                        if product.listing_time is not None
                        else None
                    ),
                    (
                        product.listing_source.value
                        if product.listing_source is not None
                        else None
                    ),
                    product.data_source.value,
                    1 if product.from_cache else 0,
                    (
                        to_utc_iso(product.last_synced_at)
                        if product.last_synced_at is not None
                        else None
                    ),
                ),
            )
        saved = self.get_product(product.store_id, product.product_id)
        if saved is None:
            raise RepositoryError("商品保存后无法读取")
        return saved

    def get_product(
        self,
        store_id: int,
        product_id: str,
    ) -> Product | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dongxiao_products
                WHERE store_id=? AND product_id=?
                """,
                (int(store_id), str(product_id)),
            ).fetchone()
        return self._product_from_row(row) if row else None

    def list_products(self, store_id: int) -> list[Product]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_products
                WHERE store_id=?
                ORDER BY product_id
                """,
                (int(store_id),),
            ).fetchall()
        return [self._product_from_row(row) for row in rows]

    def catalog_snapshot_for_products(
        self,
        store_id: int,
        product_ids: list[str] | tuple[str, ...],
    ) -> dict[str, tuple[Product, tuple[Sku, ...]]]:
        normalized = tuple(
            dict.fromkeys(
                str(product_id)
                for product_id in product_ids
                if str(product_id)
            )
        )
        if not normalized:
            return {}
        placeholders = ",".join("?" for _ in normalized)
        with self.connect() as conn:
            product_rows = conn.execute(
                f"""
                SELECT * FROM dongxiao_products
                WHERE store_id=? AND product_id IN ({placeholders})
                """,
                (int(store_id), *normalized),
            ).fetchall()
            sku_rows = conn.execute(
                f"""
                SELECT * FROM dongxiao_skus
                WHERE store_id=?
                  AND product_id IN ({placeholders})
                  AND presence_state='present'
                ORDER BY product_id, price_cents, sku_id
                """,
                (int(store_id), *normalized),
            ).fetchall()
        sku_map: dict[str, list[Sku]] = {}
        for row in sku_rows:
            sku = self._sku_from_row(row)
            sku_map.setdefault(sku.product_id, []).append(sku)
        return {
            str(row["product_id"]): (
                self._product_from_row(row),
                tuple(sku_map.get(str(row["product_id"]), ())),
            )
            for row in product_rows
        }

    def query_products(
        self,
        store_id: int,
        filters: ProductFilter,
    ) -> ProductPage:
        conditions = ["store_id=?"]
        args: list[Any] = [int(store_id)]
        if filters.keyword:
            escaped = (
                filters.keyword.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            conditions.append("title LIKE ? ESCAPE '\\'")
            args.append(f"%{escaped}%")
        if filters.states:
            placeholders = ",".join("?" for _ in filters.states)
            conditions.append(f"state IN ({placeholders})")
            args.extend(item.value for item in filters.states)
        if filters.product_ids:
            placeholders = ",".join("?" for _ in filters.product_ids)
            conditions.append(f"product_id IN ({placeholders})")
            args.extend(filters.product_ids)
        if filters.min_price_cents is not None:
            conditions.append("min_price_cents >= ?")
            args.append(filters.min_price_cents)
        if filters.max_price_cents is not None:
            conditions.append("min_price_cents <= ?")
            args.append(filters.max_price_cents)
        if (
            filters.zero_sales
            or filters.sales_min is not None
            or filters.sales_max is not None
        ):
            conditions.extend(
                (
                    "sales_count IS NOT NULL",
                    "sales_source='official_api'",
                )
            )
        if filters.zero_sales:
            conditions.append("sales_count=0")
        if filters.sales_min is not None:
            conditions.append("sales_count >= ?")
            args.append(filters.sales_min)
        if filters.sales_max is not None:
            conditions.append("sales_count <= ?")
            args.append(filters.sales_max)
        if filters.listed_from is not None or filters.listed_to is not None:
            conditions.extend(
                (
                    "listing_time IS NOT NULL",
                    "listing_source='official_api'",
                )
            )
        if filters.listed_from is not None:
            conditions.append("listing_time >= ?")
            args.append(to_utc_iso(filters.listed_from))
        if filters.listed_to is not None:
            conditions.append("listing_time <= ?")
            args.append(to_utc_iso(filters.listed_to))

        direction = "DESC" if filters.sort_desc else "ASC"
        order_by = {
            ProductSortField.PRODUCT_ID: f"product_id {direction}",
            ProductSortField.TITLE: f"title COLLATE NOCASE {direction}",
            ProductSortField.PRICE:
                f"min_price_cents {direction}, product_id",
            ProductSortField.SALES:
                "CASE WHEN sales_count IS NULL THEN 1 ELSE 0 END, "
                f"sales_count {direction}, product_id",
            ProductSortField.LISTING_TIME:
                "CASE WHEN listing_time IS NULL THEN 1 ELSE 0 END, "
                f"listing_time {direction}, product_id",
            ProductSortField.UPDATED_AT:
                f"updated_at {direction}, product_id",
        }[filters.sort_by]
        where = " AND ".join(conditions)
        with self.connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM dongxiao_products WHERE {where}",
                    args,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT * FROM dongxiao_products
                WHERE {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (
                    *args,
                    filters.page_size,
                    (filters.page - 1) * filters.page_size,
                ),
            ).fetchall()
            trusted_sales = bool(
                conn.execute(
                    """
                    SELECT 1 FROM dongxiao_products
                    WHERE store_id=?
                      AND sales_count IS NOT NULL
                      AND sales_source='official_api'
                    LIMIT 1
                    """,
                    (int(store_id),),
                ).fetchone()
            )
            trusted_listing = bool(
                conn.execute(
                    """
                    SELECT 1 FROM dongxiao_products
                    WHERE store_id=?
                      AND listing_time IS NOT NULL
                      AND listing_source='official_api'
                    LIMIT 1
                    """,
                    (int(store_id),),
                ).fetchone()
            )
            source_summary = tuple(
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT DISTINCT data_source
                    FROM dongxiao_products
                    WHERE store_id=?
                    ORDER BY data_source
                    """,
                    (int(store_id),),
                ).fetchall()
            )
        unavailable: list[str] = []
        if not trusted_sales:
            unavailable.append("sales")
        if not trusted_listing:
            unavailable.append("listing_time")
        return ProductPage(
            items=tuple(self._product_from_row(row) for row in rows),
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            page_count=math.ceil(total / filters.page_size),
            unavailable_fields=tuple(unavailable),
            source_summary=source_summary,
        )

    def save_import_preview(
        self,
        store,
        preview: ProductImportPreview,
    ) -> dict[str, int]:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, int(store.id), str(store.app_id))
            for item in preview.items:
                if item.status.value == "duplicate":
                    dedupe_key = (
                        f"duplicate:{item.line_number}:{item.dedupe_key}"
                    )
                elif item.dedupe_key:
                    dedupe_key = item.dedupe_key
                else:
                    digest = hashlib.sha256(
                        item.raw_text.encode("utf-8")
                    ).hexdigest()
                    dedupe_key = (
                        f"invalid:{item.line_number}:{digest[:24]}"
                    )
                conn.execute(
                    """
                    INSERT INTO dongxiao_product_imports(
                        import_id, store_id, store_app_id, raw_text,
                        dedupe_key, product_id, canonical_url, status,
                        reason, source_kind, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?)
                    ON CONFLICT(store_app_id, dedupe_key) DO UPDATE SET
                        raw_text=excluded.raw_text,
                        product_id=excluded.product_id,
                        canonical_url=excluded.canonical_url,
                        status=excluded.status,
                        reason=excluded.reason,
                        updated_at=excluded.updated_at
                    """,
                    (
                        uuid.uuid4().hex,
                        int(store.id),
                        str(store.app_id),
                        item.raw_text,
                        dedupe_key,
                        item.product_id,
                        item.canonical_url or None,
                        item.status.value,
                        item.reason,
                        now,
                        now,
                    ),
                )
        return {
            "saved": len(preview.items),
            "duplicates": preview.duplicate_count,
            "ready": preview.ready_count,
            "pending": preview.pending_count,
            "invalid": preview.invalid_count,
        }

    def list_import_items(
        self,
        store_id: int,
        *,
        status: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT * FROM dongxiao_product_imports
            WHERE store_id=?
        """
        args: list[Any] = [int(store_id)]
        if status:
            sql += " AND status=?"
            args.append(str(status))
        sql += " ORDER BY created_at DESC, import_id DESC LIMIT ?"
        args.append(max(1, min(5000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def upsert_sku(self, sku: Sku) -> Sku:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, sku.store_id, sku.store_app_id)
            product = conn.execute(
                """
                SELECT 1 FROM dongxiao_products
                WHERE store_app_id=? AND product_id=?
                """,
                (sku.store_app_id, sku.product_id),
            ).fetchone()
            if product is None:
                raise EntityNotFound("SKU 所属商品不存在")
            now = to_utc_iso(datetime.now(UTC))
            conn.execute(
                """
                INSERT INTO dongxiao_skus(
                    store_id, store_app_id, product_id, sku_id, specs_json,
                    price_cents, stock, available, purchase_min, purchase_max,
                    is_default, observed_at, created_at, updated_at,
                    data_complete, presence_state, missing_since,
                    purchase_min_verified
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'present', NULL, ?
                )
                ON CONFLICT(store_app_id, product_id, sku_id) DO UPDATE SET
                    store_id=excluded.store_id,
                    specs_json=excluded.specs_json,
                    price_cents=excluded.price_cents,
                    stock=excluded.stock,
                    available=excluded.available,
                    purchase_min=excluded.purchase_min,
                    purchase_max=excluded.purchase_max,
                    is_default=excluded.is_default,
                    observed_at=excluded.observed_at,
                    updated_at=excluded.updated_at,
                    data_complete=excluded.data_complete,
                    presence_state='present',
                    missing_since=NULL,
                    purchase_min_verified=excluded.purchase_min_verified
                """,
                (
                    sku.store_id,
                    sku.store_app_id,
                    sku.product_id,
                    sku.sku_id,
                    _json(sku.specs),
                    sku.price_cents,
                    sku.stock,
                    1 if sku.available else 0,
                    sku.purchase_min,
                    sku.purchase_max,
                    1 if sku.is_default else 0,
                    to_utc_iso(sku.observed_at),
                    now,
                    now,
                    1 if sku.data_complete else 0,
                    1 if sku.purchase_min_verified else 0,
                ),
            )
        saved = self.get_sku(sku.store_id, sku.product_id, sku.sku_id)
        if saved is None:
            raise RepositoryError("SKU 保存后无法读取")
        return saved

    def upsert_catalog_items(
        self,
        items: list[CatalogProductData],
        *,
        return_reconciled: bool = False,
    ) -> int | tuple[int, int]:
        if not items:
            return (0, 0) if return_reconciled else 0
        now = to_utc_iso(datetime.now(UTC))
        product_rows: list[tuple[Any, ...]] = []
        sku_rows: list[tuple[Any, ...]] = []
        snapshot_rows: list[tuple[Any, ...]] = []
        stores: set[tuple[int, str]] = set()
        for item in items:
            product = item.product
            stores.add((product.store_id, product.store_app_id))
            product_rows.append(
                (
                    product.store_id,
                    product.store_app_id,
                    product.product_id,
                    product.title,
                    product.state.value,
                    product.min_price_cents,
                    product.stock_total,
                    to_utc_iso(product.observed_at),
                    now,
                    now,
                    product.sales_count,
                    (
                        product.sales_source.value
                        if product.sales_source is not None
                        else None
                    ),
                    (
                        to_utc_iso(product.listing_time)
                        if product.listing_time is not None
                        else None
                    ),
                    (
                        product.listing_source.value
                        if product.listing_source is not None
                        else None
                    ),
                    product.data_source.value,
                    1 if product.from_cache else 0,
                    (
                        to_utc_iso(product.last_synced_at)
                        if product.last_synced_at is not None
                        else None
                    ),
                    PresenceState.PRESENT.value,
                    None,
                )
            )
            snapshot_rows.append(
                (
                    uuid.uuid4().hex,
                    product.store_id,
                    product.store_app_id,
                    product.product_id,
                    "",
                    product.title,
                    product.min_price_cents,
                    product.stock_total,
                    1 if product.state is ProductState.ACTIVE else 0,
                    product.data_source.value,
                    _json(
                        {
                            "data_source": product.data_source.value,
                            "from_cache": product.from_cache,
                            "sales_available": product.sales_count is not None,
                            "listing_available": product.listing_time is not None,
                        }
                    ),
                    to_utc_iso(product.observed_at),
                )
            )
            for sku in item.skus:
                ensure_same_store(
                    product.store_id,
                    sku.store_id,
                    label="SKU",
                )
                if sku.store_app_id != product.store_app_id:
                    raise StoreIsolationError("SKU 与商品店铺不一致")
                if sku.product_id != product.product_id:
                    raise StoreIsolationError("SKU 与商品 ID 不一致")
                sku_rows.append(
                    (
                        sku.store_id,
                        sku.store_app_id,
                        sku.product_id,
                        sku.sku_id,
                        _json(sku.specs),
                        sku.price_cents,
                        sku.stock,
                        1 if sku.available else 0,
                        sku.purchase_min,
                        sku.purchase_max,
                        1 if sku.is_default else 0,
                        to_utc_iso(sku.observed_at),
                        now,
                        now,
                        1 if sku.data_complete else 0,
                        PresenceState.PRESENT.value,
                        None,
                        1 if sku.purchase_min_verified else 0,
                    )
                )
                snapshot_rows.append(
                    (
                        uuid.uuid4().hex,
                        sku.store_id,
                        sku.store_app_id,
                        sku.product_id,
                        sku.sku_id,
                        product.title,
                        sku.price_cents,
                        sku.stock,
                        1 if sku.available else 0,
                        product.data_source.value,
                        _json(
                            {
                                "data_source": product.data_source.value,
                                "data_complete": sku.data_complete,
                            }
                        ),
                        to_utc_iso(sku.observed_at),
                    )
                )

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for store_id, store_app_id in stores:
                self._ensure_store(conn, store_id, store_app_id)
            conn.executemany(
                """
                INSERT INTO dongxiao_products(
                    store_id, store_app_id, product_id, title, state,
                    min_price_cents, stock_total, observed_at,
                    created_at, updated_at, sales_count, sales_source,
                    listing_time, listing_source, data_source,
                    from_cache, last_synced_at, presence_state, missing_since
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(store_app_id, product_id) DO UPDATE SET
                    store_id=excluded.store_id,
                    title=excluded.title,
                    state=excluded.state,
                    min_price_cents=excluded.min_price_cents,
                    stock_total=excluded.stock_total,
                    observed_at=excluded.observed_at,
                    updated_at=excluded.updated_at,
                    sales_count=excluded.sales_count,
                    sales_source=excluded.sales_source,
                    listing_time=excluded.listing_time,
                    listing_source=excluded.listing_source,
                    data_source=excluded.data_source,
                    from_cache=excluded.from_cache,
                    last_synced_at=excluded.last_synced_at,
                    presence_state=excluded.presence_state,
                    missing_since=excluded.missing_since
                """,
                product_rows,
            )
            conn.executemany(
                """
                INSERT INTO dongxiao_skus(
                    store_id, store_app_id, product_id, sku_id, specs_json,
                    price_cents, stock, available, purchase_min, purchase_max,
                    is_default, observed_at, created_at, updated_at,
                    data_complete, presence_state, missing_since,
                    purchase_min_verified
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(store_app_id, product_id, sku_id) DO UPDATE SET
                    store_id=excluded.store_id,
                    specs_json=excluded.specs_json,
                    price_cents=excluded.price_cents,
                    stock=excluded.stock,
                    available=excluded.available,
                    purchase_min=excluded.purchase_min,
                    purchase_max=excluded.purchase_max,
                    is_default=excluded.is_default,
                    observed_at=excluded.observed_at,
                    updated_at=excluded.updated_at,
                    data_complete=excluded.data_complete,
                    presence_state=excluded.presence_state,
                    missing_since=excluded.missing_since,
                    purchase_min_verified=excluded.purchase_min_verified
                """,
                sku_rows,
            )
            conn.executemany(
                """
                INSERT INTO dongxiao_product_snapshots(
                    snapshot_id, store_id, store_app_id, product_id, sku_id,
                    title, price_cents, stock, available, source,
                    details_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                snapshot_rows,
            )
            conn.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS dongxiao_seen_products_v3 (
                    store_app_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    PRIMARY KEY (store_app_id, product_id)
                )
                """
            )
            conn.execute("DELETE FROM dongxiao_seen_products_v3")
            conn.executemany(
                """
                INSERT OR IGNORE INTO dongxiao_seen_products_v3(
                    store_app_id, product_id
                ) VALUES (?, ?)
                """,
                [
                    (item.product.store_app_id, item.product.product_id)
                    for item in items
                ],
            )
            conn.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS dongxiao_seen_skus_v3 (
                    store_app_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    sku_id TEXT NOT NULL,
                    PRIMARY KEY (store_app_id, product_id, sku_id)
                )
                """
            )
            conn.execute("DELETE FROM dongxiao_seen_skus_v3")
            conn.executemany(
                """
                INSERT OR IGNORE INTO dongxiao_seen_skus_v3(
                    store_app_id, product_id, sku_id
                ) VALUES (?, ?, ?)
                """,
                [
                    (sku.store_app_id, sku.product_id, sku.sku_id)
                    for item in items
                    for sku in item.skus
                ],
            )
            conn.execute(
                """
                UPDATE dongxiao_skus AS sku
                SET presence_state='missing',
                    missing_since=COALESCE(sku.missing_since, ?),
                    available=0,
                    updated_at=?
                WHERE sku.presence_state!='missing'
                  AND EXISTS (
                      SELECT 1
                      FROM dongxiao_seen_products_v3 AS product
                      WHERE product.store_app_id=sku.store_app_id
                        AND product.product_id=sku.product_id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM dongxiao_seen_skus_v3 AS seen
                      WHERE seen.store_app_id=sku.store_app_id
                        AND seen.product_id=sku.product_id
                        AND seen.sku_id=sku.sku_id
                  )
                """,
                (now, now),
            )
            sku_reconcile_count = int(
                conn.execute("SELECT changes()").fetchone()[0]
            )
        if return_reconciled:
            return len(product_rows), sku_reconcile_count
        return len(product_rows)

    def get_sku(
        self,
        store_id: int,
        product_id: str,
        sku_id: str,
    ) -> Sku | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dongxiao_skus
                WHERE store_id=? AND product_id=? AND sku_id=?
                """,
                (int(store_id), str(product_id), str(sku_id)),
            ).fetchone()
        return self._sku_from_row(row) if row else None

    def list_skus(
        self,
        store_id: int,
        product_id: str,
        *,
        include_missing: bool = False,
    ) -> list[Sku]:
        presence_clause = (
            ""
            if include_missing
            else " AND presence_state='present'"
        )
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM dongxiao_skus
                WHERE store_id=? AND product_id=?
                {presence_clause}
                ORDER BY price_cents, sku_id
                """,
                (int(store_id), str(product_id)),
            ).fetchall()
        return [self._sku_from_row(row) for row in rows]

    def reconcile_catalog_missing(
        self,
        *,
        store_id: int,
        store_app_id: str,
        seen_product_ids: set[str] | frozenset[str],
        observed_at: datetime | None = None,
    ) -> tuple[int, int]:
        now = to_utc_iso(observed_at or datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            conn.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS dongxiao_full_catalog_seen_v3 (
                    product_id TEXT PRIMARY KEY
                )
                """
            )
            conn.execute("DELETE FROM dongxiao_full_catalog_seen_v3")
            conn.executemany(
                """
                INSERT OR IGNORE INTO dongxiao_full_catalog_seen_v3(
                    product_id
                ) VALUES (?)
                """,
                [
                    (str(product_id),)
                    for product_id in sorted(seen_product_ids)
                    if str(product_id)
                ],
            )
            product_cursor = conn.execute(
                """
                UPDATE dongxiao_products AS product
                SET presence_state='missing',
                    missing_since=COALESCE(product.missing_since, ?),
                    updated_at=?
                WHERE product.store_id=?
                  AND product.store_app_id=?
                  AND product.presence_state!='missing'
                  AND NOT EXISTS (
                      SELECT 1 FROM dongxiao_full_catalog_seen_v3 AS seen
                      WHERE seen.product_id=product.product_id
                  )
                """,
                (
                    now,
                    now,
                    int(store_id),
                    str(store_app_id),
                ),
            )
            sku_cursor = conn.execute(
                """
                UPDATE dongxiao_skus AS sku
                SET presence_state='missing',
                    missing_since=COALESCE(sku.missing_since, ?),
                    available=0,
                    updated_at=?
                WHERE sku.store_id=?
                  AND sku.store_app_id=?
                  AND sku.presence_state!='missing'
                  AND NOT EXISTS (
                      SELECT 1 FROM dongxiao_full_catalog_seen_v3 AS seen
                      WHERE seen.product_id=sku.product_id
                  )
                """,
                (
                    now,
                    now,
                    int(store_id),
                    str(store_app_id),
                ),
            )
        return int(product_cursor.rowcount), int(sku_cursor.rowcount)

    def record_catalog_sync_run(
        self,
        *,
        run_id: str,
        store_id: int,
        store_app_id: str,
        source: DataSource | str,
        status: str,
        discovered: int,
        saved: int,
        failed: int,
        reconciled_products: int,
        reconciled_skus: int,
        errors: list[str] | tuple[str, ...],
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        allowed_statuses = {"complete", "partial", "failed", "cancelled"}
        normalized_status = str(status)
        if normalized_status not in allowed_statuses:
            raise ValueError("商品同步运行状态不受支持")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            conn.execute(
                """
                INSERT INTO dongxiao_catalog_sync_runs(
                    run_id, store_id, store_app_id, source, status,
                    discovered, saved, failed, reconciled_products,
                    reconciled_skus, errors_json, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    int(store_id),
                    str(store_app_id),
                    DataSource(source).value,
                    normalized_status,
                    int(discovered),
                    int(saved),
                    int(failed),
                    int(reconciled_products),
                    int(reconciled_skus),
                    _json(list(errors)),
                    to_utc_iso(started_at),
                    to_utc_iso(finished_at),
                ),
            )

    def latest_catalog_sync_run(
        self,
        store_id: int,
        *,
        store_app_id: str = "",
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dongxiao_catalog_sync_runs
                WHERE store_id=?
                  AND (?='' OR store_app_id=?)
                ORDER BY finished_at DESC, run_id DESC
                LIMIT 1
                """,
                (int(store_id), str(store_app_id), str(store_app_id)),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["errors"] = _load_list(result.pop("errors_json", "[]"))
        return result

    def record_product_snapshot(self, snapshot: ProductSnapshot) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(
                conn,
                snapshot.store_id,
                snapshot.store_app_id,
            )
            conn.execute(
                """
                INSERT INTO dongxiao_product_snapshots(
                    snapshot_id, store_id, store_app_id, product_id, sku_id,
                    title, price_cents, stock, available, source,
                    details_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.store_id,
                    snapshot.store_app_id,
                    snapshot.product_id,
                    snapshot.sku_id,
                    snapshot.title,
                    snapshot.price_cents,
                    snapshot.stock,
                    1 if snapshot.available else 0,
                    snapshot.source,
                    _json(snapshot.details),
                    to_utc_iso(snapshot.observed_at),
                ),
            )

    def create_pool(self, pool: ProductPool) -> tuple[ProductPool, bool]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, pool.store_id, pool.store_app_id)
            existing = conn.execute(
                """
                SELECT * FROM dongxiao_product_pools
                WHERE store_app_id=? AND idempotency_key=?
                """,
                (pool.store_app_id, pool.idempotency_key),
            ).fetchone()
            if existing is not None:
                return self._pool_from_row(existing), False
            conn.execute(
                """
                INSERT INTO dongxiao_product_pools(
                    pool_id, store_id, store_app_id, name, status,
                    idempotency_key, created_at, updated_at, group_name,
                    copied_from_pool_id, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pool.pool_id,
                    pool.store_id,
                    pool.store_app_id,
                    pool.name,
                    pool.status.value,
                    pool.idempotency_key,
                    to_utc_iso(pool.created_at),
                    to_utc_iso(pool.updated_at),
                    pool.group_name,
                    pool.copied_from_pool_id or None,
                    pool.version,
                ),
            )
            self._append_pool_event(
                conn,
                pool_id=pool.pool_id,
                store_id=pool.store_id,
                store_app_id=pool.store_app_id,
                action="pool_created",
                detail={
                    "name": pool.name,
                    "group_name": pool.group_name,
                },
            )
        return pool, True

    def add_pool_item(self, item: ProductPoolItem) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, item.store_id, item.store_app_id)
            conn.execute(
                """
                INSERT INTO dongxiao_product_pool_items(
                    item_id, pool_id, store_id, store_app_id, product_id,
                    sku_id, position, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_id,
                    item.pool_id,
                    item.store_id,
                    item.store_app_id,
                    item.product_id,
                    item.sku_id,
                    item.position,
                    to_utc_iso(item.created_at),
                ),
            )
            self._append_pool_event(
                conn,
                pool_id=item.pool_id,
                store_id=item.store_id,
                store_app_id=item.store_app_id,
                action="item_added",
                product_id=item.product_id,
                sku_id=item.sku_id,
            )

    def get_pool(
        self,
        pool_id: str,
        *,
        store_id: int,
    ) -> ProductPool | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dongxiao_product_pools
                WHERE pool_id=? AND store_id=?
                """,
                (str(pool_id), int(store_id)),
            ).fetchone()
        return self._pool_from_row(row) if row else None

    def list_pools(self, store_id: int) -> list[ProductPool]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_product_pools
                WHERE store_id=?
                ORDER BY group_name COLLATE NOCASE, name COLLATE NOCASE,
                         updated_at DESC
                """,
                (int(store_id),),
            ).fetchall()
        return [self._pool_from_row(row) for row in rows]

    def rename_pool(
        self,
        pool_id: str,
        *,
        store_id: int,
        store_app_id: str,
        name: str,
        group_name: str,
    ) -> ProductPool:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("商品池名称不能为空")
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            cursor = conn.execute(
                """
                UPDATE dongxiao_product_pools
                SET name=?, group_name=?, version=version+1, updated_at=?
                WHERE pool_id=? AND store_id=? AND store_app_id=?
                """,
                (
                    normalized_name,
                    str(group_name or "").strip()[:200],
                    now,
                    str(pool_id),
                    int(store_id),
                    str(store_app_id),
                ),
            )
            if cursor.rowcount != 1:
                raise EntityNotFound("商品池不存在")
            self._append_pool_event(
                conn,
                pool_id=pool_id,
                store_id=store_id,
                store_app_id=store_app_id,
                action="pool_renamed",
                detail={
                    "name": normalized_name,
                    "group_name": str(group_name or "").strip()[:200],
                },
            )
        pool = self.get_pool(pool_id, store_id=store_id)
        if pool is None:
            raise RepositoryError("商品池更新后无法读取")
        return pool

    def add_pool_products(
        self,
        pool_id: str,
        *,
        store_id: int,
        store_app_id: str,
        products: list[tuple[str, str]],
    ) -> dict[str, int]:
        now = to_utc_iso(datetime.now(UTC))
        added = 0
        skipped = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            pool = conn.execute(
                """
                SELECT 1 FROM dongxiao_product_pools
                WHERE pool_id=? AND store_id=? AND store_app_id=?
                """,
                (str(pool_id), int(store_id), str(store_app_id)),
            ).fetchone()
            if pool is None:
                raise EntityNotFound("商品池不存在")
            for raw_product_id, raw_sku_id in products:
                product_id = str(raw_product_id or "").strip()
                sku_id = str(raw_sku_id or "").strip()
                if not product_id:
                    skipped += 1
                    continue
                exists = conn.execute(
                    """
                    SELECT 1 FROM dongxiao_products
                    WHERE store_id=? AND store_app_id=? AND product_id=?
                    """,
                    (int(store_id), str(store_app_id), product_id),
                ).fetchone()
                if exists is None:
                    skipped += 1
                    continue
                if sku_id:
                    sku_exists = conn.execute(
                        """
                        SELECT 1 FROM dongxiao_skus
                        WHERE store_id=? AND store_app_id=?
                          AND product_id=? AND sku_id=?
                        """,
                        (
                            int(store_id),
                            str(store_app_id),
                            product_id,
                            sku_id,
                        ),
                    ).fetchone()
                    if sku_exists is None:
                        skipped += 1
                        continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO dongxiao_product_pool_items(
                        item_id, pool_id, store_id, store_app_id, product_id,
                        sku_id, position, created_at
                    )
                    SELECT ?, ?, ?, ?, ?, ?,
                           COALESCE(MAX(position), -1) + 1, ?
                    FROM dongxiao_product_pool_items
                    WHERE pool_id=?
                    """,
                    (
                        uuid.uuid4().hex,
                        str(pool_id),
                        int(store_id),
                        str(store_app_id),
                        product_id,
                        sku_id,
                        now,
                        str(pool_id),
                    ),
                )
                if cursor.rowcount == 1:
                    added += 1
                    self._append_pool_event(
                        conn,
                        pool_id=pool_id,
                        store_id=store_id,
                        store_app_id=store_app_id,
                        action="item_added",
                        product_id=product_id,
                        sku_id=sku_id,
                    )
                else:
                    skipped += 1
        return {"added": added, "skipped": skipped}

    def remove_pool_item(
        self,
        pool_id: str,
        item_id: str,
        *,
        store_id: int,
        store_app_id: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            row = conn.execute(
                """
                SELECT product_id, sku_id
                FROM dongxiao_product_pool_items
                WHERE item_id=? AND pool_id=? AND store_id=? AND store_app_id=?
                """,
                (
                    str(item_id),
                    str(pool_id),
                    int(store_id),
                    str(store_app_id),
                ),
            ).fetchone()
            if row is None:
                raise EntityNotFound("商品池明细不存在")
            conn.execute(
                """
                DELETE FROM dongxiao_product_pool_items
                WHERE item_id=? AND pool_id=? AND store_id=? AND store_app_id=?
                """,
                (
                    str(item_id),
                    str(pool_id),
                    int(store_id),
                    str(store_app_id),
                ),
            )
            self._append_pool_event(
                conn,
                pool_id=pool_id,
                store_id=store_id,
                store_app_id=store_app_id,
                action="item_removed",
                product_id=str(row["product_id"]),
                sku_id=str(row["sku_id"]),
            )

    def reorder_pool_items(
        self,
        pool_id: str,
        item_ids: list[str],
        *,
        store_id: int,
        store_app_id: str,
    ) -> None:
        ordered = list(dict.fromkeys(str(item).strip() for item in item_ids))
        if not ordered or any(not item for item in ordered):
            raise ValueError("商品池排序列表不能为空")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            existing = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT item_id FROM dongxiao_product_pool_items
                    WHERE pool_id=? AND store_id=? AND store_app_id=?
                    """,
                    (str(pool_id), int(store_id), str(store_app_id)),
                ).fetchall()
            }
            if set(ordered) != existing:
                raise ValueError("排序列表必须包含商品池中的全部明细")
            conn.executemany(
                """
                UPDATE dongxiao_product_pool_items
                SET position=?
                WHERE item_id=? AND pool_id=? AND store_id=? AND store_app_id=?
                """,
                [
                    (
                        position,
                        item_id,
                        str(pool_id),
                        int(store_id),
                        str(store_app_id),
                    )
                    for position, item_id in enumerate(ordered)
                ],
            )
            self._append_pool_event(
                conn,
                pool_id=pool_id,
                store_id=store_id,
                store_app_id=store_app_id,
                action="items_reordered",
                detail={"item_ids": ordered},
            )

    def list_pool_items(
        self,
        pool_id: str,
        *,
        store_id: int,
        store_app_id: str,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            self._ensure_store(conn, store_id, store_app_id)
            rows = conn.execute(
                """
                SELECT item.item_id, item.pool_id, item.product_id,
                       item.sku_id, item.position, item.created_at,
                       product.title, product.state,
                       product.min_price_cents, product.stock_total,
                       product.presence_state,
                       store.name AS store_name
                FROM dongxiao_product_pool_items AS item
                LEFT JOIN dongxiao_products AS product
                  ON product.store_app_id=item.store_app_id
                 AND product.store_id=item.store_id
                 AND product.product_id=item.product_id
                LEFT JOIN stores AS store ON store.id=item.store_id
                WHERE item.pool_id=?
                  AND item.store_id=?
                  AND item.store_app_id=?
                ORDER BY item.position, item.item_id
                """,
                (str(pool_id), int(store_id), str(store_app_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def copy_pool(
        self,
        source_pool_id: str,
        *,
        new_pool_id: str,
        store_id: int,
        store_app_id: str,
        new_name: str,
        group_name: str = "",
    ) -> ProductPool:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            source = conn.execute(
                """
                SELECT * FROM dongxiao_product_pools
                WHERE pool_id=? AND store_id=? AND store_app_id=?
                """,
                (str(source_pool_id), int(store_id), str(store_app_id)),
            ).fetchone()
            if source is None:
                raise EntityNotFound("源商品池不存在")
            normalized_name = str(new_name or "").strip()
            if not normalized_name:
                raise ValueError("新商品池名称不能为空")
            conn.execute(
                """
                INSERT INTO dongxiao_product_pools(
                    pool_id, store_id, store_app_id, name, status,
                    idempotency_key, created_at, updated_at, group_name,
                    copied_from_pool_id, version
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, 1)
                """,
                (
                    str(new_pool_id),
                    int(store_id),
                    str(store_app_id),
                    normalized_name,
                    f"copy:{uuid.uuid4().hex}",
                    now,
                    now,
                    str(group_name or "").strip()[:200],
                    str(source_pool_id),
                ),
            )
            items = conn.execute(
                """
                SELECT product_id, sku_id, position
                FROM dongxiao_product_pool_items
                WHERE pool_id=? AND store_id=? AND store_app_id=?
                ORDER BY position, item_id
                """,
                (str(source_pool_id), int(store_id), str(store_app_id)),
            ).fetchall()
            conn.executemany(
                """
                INSERT INTO dongxiao_product_pool_items(
                    item_id, pool_id, store_id, store_app_id, product_id,
                    sku_id, position, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        uuid.uuid4().hex,
                        str(new_pool_id),
                        int(store_id),
                        str(store_app_id),
                        str(row["product_id"]),
                        str(row["sku_id"]),
                        int(row["position"]),
                        now,
                    )
                    for row in items
                ],
            )
            self._append_pool_event(
                conn,
                pool_id=new_pool_id,
                store_id=store_id,
                store_app_id=store_app_id,
                action="pool_copied",
                detail={"source_pool_id": source_pool_id},
            )
        pool = self.get_pool(new_pool_id, store_id=store_id)
        if pool is None:
            raise RepositoryError("复制后的商品池无法读取")
        return pool

    def pool_stats(
        self,
        pool_id: str,
        *,
        store_id: int,
        store_app_id: str,
    ) -> dict[str, int]:
        with self.connect() as conn:
            self._ensure_store(conn, store_id, store_app_id)
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS item_count,
                    COUNT(DISTINCT product_id) AS product_count,
                    SUM(
                        CASE WHEN length(trim(sku_id)) > 0 THEN 1 ELSE 0 END
                    ) AS sku_level_count,
                    SUM(
                        CASE WHEN length(trim(sku_id)) = 0 THEN 1 ELSE 0 END
                    ) AS product_level_count
                FROM dongxiao_product_pool_items
                WHERE pool_id=? AND store_id=? AND store_app_id=?
                """,
                (str(pool_id), int(store_id), str(store_app_id)),
            ).fetchone()
        assert row is not None
        return {
            "item_count": int(row["item_count"] or 0),
            "product_count": int(row["product_count"] or 0),
            "sku_level_count": int(row["sku_level_count"] or 0),
            "product_level_count": int(row["product_level_count"] or 0),
        }

    def list_pool_events(
        self,
        pool_id: str,
        *,
        store_id: int,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_product_pool_events
                WHERE pool_id=? AND store_id=?
                ORDER BY created_at DESC, event_id DESC
                """,
                (str(pool_id), int(store_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_pool_event(self, event: ProductPoolEvent) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(
                conn,
                event.store_id,
                event.store_app_id,
            )
            conn.execute(
                """
                INSERT INTO dongxiao_product_pool_events(
                    event_id, pool_id, store_id, store_app_id, action,
                    product_id, sku_id, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.pool_id,
                    event.store_id,
                    event.store_app_id,
                    event.action,
                    event.product_id,
                    event.sku_id,
                    _json(event.detail),
                    to_utc_iso(event.created_at),
                ),
            )

    def create_task(self, task: Task) -> tuple[Task, bool]:
        return self.create_task_with_execution_items(
            task,
            [],
            verify_execution_items=False,
        )

    def create_task_with_execution_items(
        self,
        task: Task,
        execution_items: list[TaskExecutionItem],
        *,
        verify_execution_items: bool = True,
    ) -> tuple[Task, bool]:
        fingerprint = task_request_fingerprint(
            task_type=task.task_type,
            store_app_id=task.store_app_id,
            metadata=task.metadata,
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, task.store_id, task.store_app_id)
            existing = conn.execute(
                """
                SELECT task_id, request_fingerprint
                FROM dongxiao_tasks
                WHERE store_app_id=? AND idempotency_key=?
                """,
                (task.store_app_id, task.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise IdempotencyConflict(str(existing["task_id"]))
                saved = self._get_task_in_connection(
                    conn,
                    str(existing["task_id"]),
                    task.store_app_id,
                )
                if saved is None:
                    raise RepositoryError("幂等任务无法读取")
                if verify_execution_items:
                    item_count = int(
                        conn.execute(
                            """
                            SELECT COUNT(*)
                            FROM dongxiao_task_execution_items
                            WHERE task_id=? AND store_app_id=?
                            """,
                            (saved.task_id, saved.store_app_id),
                        ).fetchone()[0]
                    )
                    if item_count != saved.total_items:
                        raise RepositoryError("幂等任务的历史明细不完整")
                return saved, False
            conn.execute(
                """
                INSERT INTO dongxiao_tasks(
                    task_id, store_id, store_app_id, task_type,
                    idempotency_key, request_fingerprint, status,
                    total_items, succeeded_items, failed_items,
                    status_reason, metadata_json, created_at, updated_at,
                    priority, max_attempts, next_run_at, lease_owner,
                    lease_expires_at, last_heartbeat_at, started_at,
                    finished_at, last_error, execution_mode
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    task.task_id,
                    task.store_id,
                    task.store_app_id,
                    task.task_type,
                    task.idempotency_key,
                    fingerprint,
                    task.status.value,
                    task.total_items,
                    task.succeeded_items,
                    task.failed_items,
                    task.status_reason,
                    _json(task.metadata),
                    to_utc_iso(task.created_at),
                    to_utc_iso(task.updated_at),
                    task.priority,
                    task.max_attempts,
                    (
                        to_utc_iso(task.next_run_at)
                        if task.next_run_at is not None
                        else None
                    ),
                    task.lease_owner,
                    (
                        to_utc_iso(task.lease_expires_at)
                        if task.lease_expires_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(task.last_heartbeat_at)
                        if task.last_heartbeat_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(task.started_at)
                        if task.started_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(task.finished_at)
                        if task.finished_at is not None
                        else None
                    ),
                    task.last_error,
                    task.execution_mode,
                ),
            )
            self._insert_audit(
                conn,
                store_id=task.store_id,
                store_app_id=task.store_app_id,
                actor_id="",
                action="task_created",
                entity_type="task",
                entity_id=task.task_id,
                request_id="",
                result=AuditResult.SUCCESS,
                detail={"idempotency_key": task.idempotency_key},
            )
            if execution_items:
                if len(execution_items) != task.total_items:
                    raise ValueError("任务明细数量与任务总项数不一致")
                if any(
                    item.task_id != task.task_id
                    or item.store_id != task.store_id
                    or item.store_app_id != task.store_app_id
                    for item in execution_items
                ):
                    raise ValueError("任务明细归属与任务不一致")
                self._insert_task_execution_items_in_connection(
                    conn,
                    execution_items,
                )
        return task, True

    def get_task(self, task_id: str, *, store_app_id: str) -> Task | None:
        with self.connect() as conn:
            return self._get_task_in_connection(
                conn,
                task_id,
                store_app_id,
            )

    def list_tasks(
        self,
        store_id: int,
        *,
        limit: int = 2000,
    ) -> list[Task]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_tasks
                WHERE store_id=?
                ORDER BY created_at DESC, task_id
                LIMIT ?
                """,
                (
                    int(store_id),
                    max(1, min(10000, int(limit))),
                ),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def list_tasks_by_store_app_id(
        self,
        store_app_id: str,
        *,
        limit: int = 2000,
    ) -> list[Task]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_tasks
                WHERE store_app_id=?
                ORDER BY created_at DESC, task_id
                LIMIT ?
                """,
                (str(store_app_id), max(1, min(10000, int(limit)))),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def transition_task(
        self,
        task_id: str,
        *,
        store_id: int,
        store_app_id: str,
        event: TaskEvent | str,
        actor_id: str = "",
        reason: str = "",
        remote_result_verified: bool = False,
    ) -> Task:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            row = conn.execute(
                """
                SELECT * FROM dongxiao_tasks
                WHERE task_id=?
                """,
                (str(task_id),),
            ).fetchone()
            if row is None:
                raise EntityNotFound("任务不存在")
            ensure_same_store(
                store_id,
                int(row["store_id"]),
                label="任务",
            )
            if str(row["store_app_id"]) != str(store_app_id):
                raise StoreIsolationError("任务不属于当前店铺")
            target = transition_task(
                str(row["status"]),
                event,
                remote_result_verified=remote_result_verified,
            )
            now = to_utc_iso(datetime.now(UTC))
            conn.execute(
                """
                UPDATE dongxiao_tasks
                SET status=?, status_reason=?, updated_at=?
                WHERE task_id=? AND store_app_id=? AND status=?
                """,
                (
                    target.value,
                    str(reason)[:1000],
                    now,
                    str(task_id),
                    str(store_app_id),
                    str(row["status"]),
                ),
            )
            self._insert_audit(
                conn,
                store_id=store_id,
                store_app_id=store_app_id,
                actor_id=actor_id,
                action="task_status_changed",
                entity_type="task",
                entity_id=str(task_id),
                request_id="",
                result=AuditResult.SUCCESS,
                detail={
                    "from": str(row["status"]),
                    "event": TaskEvent(event).value,
                    "to": target.value,
                    "reason": reason,
                },
            )
            saved = self._get_task_in_connection(
                conn,
                str(task_id),
                str(store_app_id),
            )
        if saved is None:
            raise RepositoryError("任务状态更新后无法读取")
        return saved

    def add_task_item(self, item: TaskItem) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, item.store_id, item.store_app_id)
            conn.execute(
                """
                INSERT INTO dongxiao_task_items(
                    item_id, task_id, store_id, store_app_id, product_id,
                    sku_id, status, attempt_count, reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_id,
                    item.task_id,
                    item.store_id,
                    item.store_app_id,
                    item.product_id,
                    item.sku_id,
                    item.status.value,
                    item.attempt_count,
                    item.reason,
                    to_utc_iso(item.created_at),
                    to_utc_iso(item.updated_at),
                ),
            )

    def add_task_execution_items(
        self,
        items: list[TaskExecutionItem],
    ) -> None:
        if not items:
            return
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_task_execution_items_in_connection(conn, items)

    def _insert_task_execution_items_in_connection(
        self,
        conn: sqlite3.Connection,
        items: list[TaskExecutionItem],
    ) -> None:
        for store_id, store_app_id in {
            (item.store_id, item.store_app_id) for item in items
        }:
            self._ensure_store(conn, store_id, store_app_id)
        for task_id, store_app_id in {
            (item.task_id, item.store_app_id) for item in items
        }:
            task = conn.execute(
                """
                SELECT total_items FROM dongxiao_tasks
                WHERE task_id=? AND store_app_id=?
                """,
                (task_id, store_app_id),
            ).fetchone()
            if task is None:
                raise EntityNotFound("执行明细所属任务不存在")
        conn.executemany(
            """
            INSERT INTO dongxiao_task_execution_items(
                item_id, task_id, store_id, store_app_id,
                subject_type, subject_id, product_id, sku_id,
                status, attempt_count, max_attempts, next_attempt_at,
                started_at, finished_at, last_error_code,
                last_error_message, payload_json, result_json,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            [
                (
                    item.item_id,
                    item.task_id,
                    item.store_id,
                    item.store_app_id,
                    item.subject_type,
                    item.subject_id,
                    item.product_id,
                    item.sku_id,
                    item.status.value,
                    item.attempt_count,
                    item.max_attempts,
                    (
                        to_utc_iso(item.next_attempt_at)
                        if item.next_attempt_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(item.started_at)
                        if item.started_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(item.finished_at)
                        if item.finished_at is not None
                        else None
                    ),
                    item.last_error_code,
                    item.last_error_message,
                    _json(item.payload),
                    _json(item.result),
                    to_utc_iso(item.created_at),
                    to_utc_iso(item.updated_at),
                )
                for item in items
            ],
        )

    def list_task_execution_items(
        self,
        task_id: str,
        *,
        store_app_id: str,
        statuses: tuple[TaskItemStatus, ...] = (),
        limit: int = 5000,
    ) -> list[TaskExecutionItem]:
        sql = """
            SELECT * FROM dongxiao_task_execution_items
            WHERE task_id=? AND store_app_id=?
        """
        args: list[Any] = [str(task_id), str(store_app_id)]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            args.extend(status.value for status in statuses)
        sql += " ORDER BY rowid LIMIT ?"
        args.append(max(1, min(100000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._execution_item_from_row(row) for row in rows]

    def claim_next_execution_item(
        self,
        task_id: str,
        *,
        store_app_id: str,
        owner: str,
    ) -> TaskExecutionItem | None:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM dongxiao_task_execution_items
                WHERE task_id=? AND store_app_id=?
                  AND status='pending'
                  AND (
                      next_attempt_at IS NULL
                      OR next_attempt_at<=?
                  )
                ORDER BY rowid
                LIMIT 1
                """,
                (str(task_id), str(store_app_id), now),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE dongxiao_task_execution_items
                SET status='running', attempt_count=attempt_count+1,
                    started_at=?, next_attempt_at=NULL,
                    last_error_code='', last_error_message='',
                    updated_at=?
                WHERE item_id=? AND status='pending'
                """,
                (now, now, str(row["item_id"])),
            )
            saved = conn.execute(
                """
                SELECT * FROM dongxiao_task_execution_items
                WHERE item_id=? AND store_app_id=?
                """,
                (str(row["item_id"]), str(store_app_id)),
            ).fetchone()
        if saved is None:
            raise RepositoryError("任务执行明细领取后无法读取")
        del owner
        return self._execution_item_from_row(saved)

    def claim_pending_execution_items(
        self,
        task_id: str,
        *,
        store_app_id: str,
        owner: str,
        limit: int = 100,
    ) -> list[TaskExecutionItem]:
        del owner
        now = to_utc_iso(datetime.now(UTC))
        safe_limit = max(1, min(500, int(limit)))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT rowid FROM dongxiao_task_execution_items
                WHERE task_id=? AND store_app_id=?
                  AND status='pending'
                  AND (
                      next_attempt_at IS NULL
                      OR next_attempt_at<=?
                  )
                ORDER BY rowid
                LIMIT ?
                """,
                (str(task_id), str(store_app_id), now, safe_limit),
            ).fetchall()
            if not rows:
                return []
            rowids = [int(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in rowids)
            conn.execute(
                f"""
                UPDATE dongxiao_task_execution_items
                SET status='running', attempt_count=attempt_count+1,
                    started_at=?, next_attempt_at=NULL,
                    last_error_code='', last_error_message='',
                    updated_at=?
                WHERE rowid IN ({placeholders})
                """,
                (now, now, *rowids),
            )
            saved_rows = conn.execute(
                f"""
                SELECT * FROM dongxiao_task_execution_items
                WHERE rowid IN ({placeholders})
                ORDER BY rowid
                """,
                tuple(rowids),
            ).fetchall()
        return [
            self._execution_item_from_row(row)
            for row in saved_rows
        ]

    def finish_task_execution_item(
        self,
        item_id: str,
        *,
        store_app_id: str,
        status: TaskItemStatus | str,
        result: dict[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
        next_attempt_at: datetime | None = None,
    ) -> bool:
        item_status = TaskItemStatus(status)
        now = to_utc_iso(datetime.now(UTC))
        finished_at = (
            now
            if item_status is not TaskItemStatus.PENDING
            else None
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE dongxiao_task_execution_items
                SET status=?, finished_at=?, next_attempt_at=?,
                    last_error_code=?, last_error_message=?,
                    result_json=?, updated_at=?
                WHERE item_id=? AND store_app_id=? AND status='running'
                """,
                (
                    item_status.value,
                    finished_at,
                    (
                        to_utc_iso(next_attempt_at)
                        if next_attempt_at is not None
                        else None
                    ),
                    str(error_code)[:200],
                    str(error_message)[:1000],
                    _json(result or {}),
                    now,
                    str(item_id),
                    str(store_app_id),
                ),
            )
            if cursor.rowcount == 1:
                return True
            existing = conn.execute(
                """
                SELECT 1 FROM dongxiao_task_execution_items
                WHERE item_id=? AND store_app_id=?
                """,
                (str(item_id), str(store_app_id)),
            ).fetchone()
        if existing is None:
            raise EntityNotFound("任务执行明细不存在")
        return False

    def mark_execution_item_verified(
        self,
        item_id: str,
        *,
        store_app_id: str,
        result: dict[str, Any],
    ) -> None:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE dongxiao_task_execution_items
                SET status='succeeded', finished_at=?, next_attempt_at=NULL,
                    last_error_code='', last_error_message='',
                    result_json=?, updated_at=?
                WHERE item_id=? AND store_app_id=? AND status='unknown'
                """,
                (
                    now,
                    _json(result),
                    now,
                    str(item_id),
                    str(store_app_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("执行明细不是可核验的 unknown 状态")

    def finish_task_execution_items_batch(
        self,
        *,
        store_app_id: str,
        updates: list[
            tuple[
                str,
                TaskItemStatus,
                dict[str, Any],
                str,
                str,
                datetime | None,
            ]
        ],
        attempts: list[TaskAttempt],
    ) -> None:
        if not updates:
            return
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                """
                UPDATE dongxiao_task_execution_items
                SET status=?, finished_at=?, next_attempt_at=?,
                    last_error_code=?, last_error_message=?,
                    result_json=?, updated_at=?
                WHERE item_id=? AND store_app_id=? AND status='running'
                """,
                [
                    (
                        status.value,
                        (
                            now
                            if status is not TaskItemStatus.PENDING
                            else None
                        ),
                        (
                            to_utc_iso(next_attempt_at)
                            if next_attempt_at is not None
                            else None
                        ),
                        str(error_code)[:200],
                        str(error_message)[:1000],
                        _json(result),
                        now,
                        item_id,
                        str(store_app_id),
                    )
                    for (
                        item_id,
                        status,
                        result,
                        error_code,
                        error_message,
                        next_attempt_at,
                    ) in updates
                ],
            )
            conn.executemany(
                """
                INSERT INTO dongxiao_execution_attempts(
                    attempt_id, task_id, item_id, store_id, store_app_id,
                    attempt_no, status, request_id, started_at, finished_at,
                    error_code, error_message, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        attempt.attempt_id,
                        attempt.task_id,
                        attempt.item_id,
                        attempt.store_id,
                        attempt.store_app_id,
                        attempt.attempt_no,
                        attempt.status.value,
                        attempt.request_id,
                        to_utc_iso(attempt.started_at),
                        (
                            to_utc_iso(attempt.finished_at)
                            if attempt.finished_at is not None
                            else None
                        ),
                        attempt.error_code,
                        attempt.error_message,
                        _json(attempt.result),
                    )
                    for attempt in attempts
                ],
            )

    def update_task_progress(
        self,
        task_id: str,
        *,
        store_id: int,
        store_app_id: str,
        succeeded_items: int,
        failed_items: int,
        status_reason: str = "",
        last_error: str = "",
        next_run_at: datetime | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> Task:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            conn.execute(
                """
                UPDATE dongxiao_tasks
                SET succeeded_items=?, failed_items=?, status_reason=?,
                    last_error=?, next_run_at=?, started_at=?,
                    finished_at=?, updated_at=?
                WHERE task_id=? AND store_app_id=?
                """,
                (
                    max(0, int(succeeded_items)),
                    max(0, int(failed_items)),
                    str(status_reason)[:1000],
                    str(last_error)[:1000],
                    (
                        to_utc_iso(next_run_at)
                        if next_run_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(started_at)
                        if started_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(finished_at)
                        if finished_at is not None
                        else None
                    ),
                    now,
                    str(task_id),
                    str(store_app_id),
                ),
            )
            saved = self._get_task_in_connection(
                conn,
                str(task_id),
                str(store_app_id),
            )
        if saved is None:
            raise EntityNotFound("任务不存在")
        return saved

    def cancel_pending_execution_items(
        self,
        task_id: str,
        *,
        store_app_id: str,
    ) -> int:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE dongxiao_task_execution_items
                SET status='cancelled', finished_at=?, updated_at=?
                WHERE task_id=? AND store_app_id=?
                  AND status IN ('pending', 'running', 'unknown')
                """,
                (now, now, str(task_id), str(store_app_id)),
            )
        return int(cursor.rowcount)

    def retry_failed_execution_items(
        self,
        task_id: str,
        *,
        store_app_id: str,
    ) -> int:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE dongxiao_task_execution_items
                SET status='pending', next_attempt_at=NULL,
                    finished_at=NULL, last_error_code='',
                    last_error_message='', updated_at=?
                WHERE task_id=? AND store_app_id=?
                  AND status='failed'
                  AND attempt_count<max_attempts
                """,
                (now, str(task_id), str(store_app_id)),
            )
        return int(cursor.rowcount)

    def recover_interrupted_tasks(self) -> int:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            local_tasks = conn.execute(
                """
                SELECT task_id, store_app_id
                FROM dongxiao_tasks
                WHERE status IN ('preparing', 'running')
                  AND execution_mode IN ('local_safe', 'official_read')
                """
            ).fetchall()
            platform_tasks = conn.execute(
                """
                SELECT task_id, store_app_id
                FROM dongxiao_tasks
                WHERE status IN ('preparing', 'running')
                  AND execution_mode='platform_write'
                """
            ).fetchall()
            for row in local_tasks:
                conn.execute(
                    """
                    UPDATE dongxiao_task_execution_items
                    SET status='pending', updated_at=?
                    WHERE task_id=? AND store_app_id=?
                      AND status='running'
                    """,
                    (now, row["task_id"], row["store_app_id"]),
                )
                conn.execute(
                    """
                    UPDATE dongxiao_tasks
                    SET status='preparing', lease_owner='',
                        lease_expires_at=NULL, updated_at=?
                    WHERE task_id=? AND store_app_id=?
                    """,
                    (now, row["task_id"], row["store_app_id"]),
                )
            for row in platform_tasks:
                conn.execute(
                    """
                    UPDATE dongxiao_task_execution_items
                    SET status='unknown', updated_at=?
                    WHERE task_id=? AND store_app_id=?
                      AND status='running'
                    """,
                    (now, row["task_id"], row["store_app_id"]),
                )
                conn.execute(
                    """
                    UPDATE dongxiao_tasks
                    SET status='unknown',
                        status_reason='程序中断，平台写操作结果需先核验',
                        lease_owner='', lease_expires_at=NULL, updated_at=?
                    WHERE task_id=? AND store_app_id=?
                    """,
                    (now, row["task_id"], row["store_app_id"]),
                )
        return len(local_tasks) + len(platform_tasks)

    def claim_task_lease(
        self,
        task_id: str,
        *,
        store_app_id: str,
        owner: str,
        lease_seconds: int = 300,
    ) -> bool:
        now_dt = datetime.now(UTC)
        now = to_utc_iso(now_dt)
        expires = to_utc_iso(
            now_dt + timedelta(seconds=max(30, int(lease_seconds)))
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE dongxiao_tasks
                SET lease_owner=?, lease_expires_at=?,
                    last_heartbeat_at=?, updated_at=?
                WHERE task_id=? AND store_app_id=?
                  AND status IN ('preparing', 'running')
                  AND (
                      lease_owner=''
                      OR lease_expires_at IS NULL
                      OR lease_expires_at<=?
                      OR lease_owner=?
                  )
                """,
                (
                    str(owner),
                    expires,
                    now,
                    now,
                    str(task_id),
                    str(store_app_id),
                    now,
                    str(owner),
                ),
            )
        return cursor.rowcount == 1

    def release_task_lease(
        self,
        task_id: str,
        *,
        store_app_id: str,
        owner: str,
    ) -> None:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE dongxiao_tasks
                SET lease_owner='', lease_expires_at=NULL, updated_at=?
                WHERE task_id=? AND store_app_id=? AND lease_owner=?
                """,
                (now, str(task_id), str(store_app_id), str(owner)),
            )

    def recover_expired_task_leases(self) -> int:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT task_id, store_app_id, execution_mode
                FROM dongxiao_tasks
                WHERE status IN ('preparing', 'running')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at<=?
                """,
                (now,),
            ).fetchall()
            for row in rows:
                if row["execution_mode"] == "platform_write":
                    conn.execute(
                        """
                        UPDATE dongxiao_task_execution_items
                        SET status='unknown', updated_at=?
                        WHERE task_id=? AND store_app_id=?
                          AND status='running'
                        """,
                        (now, row["task_id"], row["store_app_id"]),
                    )
                    conn.execute(
                        """
                        UPDATE dongxiao_tasks
                        SET status='unknown',
                            status_reason='任务租约过期，平台写结果需核验',
                            lease_owner='', lease_expires_at=NULL, updated_at=?
                        WHERE task_id=? AND store_app_id=?
                        """,
                        (now, row["task_id"], row["store_app_id"]),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE dongxiao_task_execution_items
                        SET status='pending', updated_at=?
                        WHERE task_id=? AND store_app_id=?
                          AND status='running'
                        """,
                        (now, row["task_id"], row["store_app_id"]),
                    )
                    conn.execute(
                        """
                        UPDATE dongxiao_tasks
                        SET status='preparing', lease_owner='',
                            lease_expires_at=NULL, updated_at=?
                        WHERE task_id=? AND store_app_id=?
                        """,
                        (now, row["task_id"], row["store_app_id"]),
                    )
        return len(rows)

    def recover_running_task_items(
        self,
        task_id: str,
        *,
        store_app_id: str,
    ) -> str:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                """
                SELECT execution_mode FROM dongxiao_tasks
                WHERE task_id=? AND store_app_id=?
                """,
                (str(task_id), str(store_app_id)),
            ).fetchone()
            if task is None:
                raise EntityNotFound("任务不存在")
            target = (
                "unknown"
                if str(task["execution_mode"]) == "platform_write"
                else "pending"
            )
            conn.execute(
                """
                UPDATE dongxiao_task_execution_items
                SET status=?, updated_at=?
                WHERE task_id=? AND store_app_id=? AND status='running'
                """,
                (target, now, str(task_id), str(store_app_id)),
            )
            if target == "unknown":
                conn.execute(
                    """
                    UPDATE dongxiao_tasks
                    SET status='unknown',
                        status_reason='存在无法确认的平台写执行明细',
                        lease_owner='', lease_expires_at=NULL, updated_at=?
                    WHERE task_id=? AND store_app_id=?
                    """,
                    (now, str(task_id), str(store_app_id)),
                )
            return target

    def list_runnable_tasks(
        self,
        *,
        limit: int = 20,
    ) -> list[Task]:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_tasks
                WHERE status IN ('pending', 'preparing', 'running')
                  AND (next_run_at IS NULL OR next_run_at<=?)
                  AND store_id IS NOT NULL
                  AND (
                      lease_owner=''
                      OR lease_expires_at IS NULL
                      OR lease_expires_at<=?
                      OR status='pending'
                  )
                ORDER BY priority DESC, created_at, task_id
                LIMIT ?
                """,
                (now, now, max(1, min(500, int(limit)))),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def platform_writes_enabled(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT setting_value
                FROM dongxiao_runtime_settings
                WHERE setting_key='platform_writes_enabled'
                """
            ).fetchone()
        return row is not None and str(row["setting_value"]) == "1"

    def set_platform_writes_enabled(
        self,
        enabled: bool,
        *,
        actor_id: str,
    ) -> bool:
        if not isinstance(enabled, bool):
            raise TypeError("平台写操作开关必须是布尔值")
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                """
                SELECT setting_value
                FROM dongxiao_runtime_settings
                WHERE setting_key='platform_writes_enabled'
                """
            ).fetchone()
            conn.execute(
                """
                INSERT INTO dongxiao_runtime_settings(
                    setting_key, setting_value, updated_by, updated_at
                ) VALUES(
                    'platform_writes_enabled', ?, ?, ?
                )
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value=excluded.setting_value,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                ("1" if enabled else "0", str(actor_id), now),
            )
            self._insert_audit(
                conn,
                store_id=None,
                store_app_id="",
                actor_id=str(actor_id),
                action="platform_write_guard_changed",
                entity_type="runtime",
                entity_id="platform_writes_enabled",
                request_id="",
                result=AuditResult.INFO,
                detail={
                    "enabled": enabled,
                    "previous_enabled": (
                        previous is not None
                        and str(previous["setting_value"]) == "1"
                    ),
                    "message": (
                        "已允许创建和执行经人工确认的平台写任务"
                        if enabled
                        else "已禁止所有新的平台写任务和执行"
                    ),
                },
            )
        return enabled

    def schema_migration_summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT version, name, checksum, applied_at
                FROM dongxiao_schema_migrations
                ORDER BY version
                """
            ).fetchall()
        return {
            "versions": [int(row["version"]) for row in rows],
            "latest": int(rows[-1]["version"]) if rows else 0,
            "latest_name": str(rows[-1]["name"]) if rows else "",
            "latest_checksum": str(rows[-1]["checksum"]) if rows else "",
            "applied_at": str(rows[-1]["applied_at"]) if rows else "",
        }

    def integrity_check(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"

    def audit_log_health(self) -> dict[str, Any]:
        with self.connect() as conn:
            triggers = {
                str(row["name"])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='trigger'
                      AND name IN (
                          'prevent_dongxiao_audit_update_v5',
                          'prevent_dongxiao_audit_update',
                          'prevent_dongxiao_audit_delete'
                      )
                    """
                ).fetchall()
            }
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM dongxiao_audit_events"
                ).fetchone()[0]
            )
        return {
            "append_only": bool(
                {
                    "prevent_dongxiao_audit_update_v5",
                    "prevent_dongxiao_audit_delete",
                }.issubset(triggers)
            ),
            "event_count": count,
        }

    def find_task_for_execution_subject(
        self,
        *,
        store_app_id: str,
        subject_type: str,
        subject_id: str,
        task_type: str,
    ) -> Task | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT task.* FROM dongxiao_tasks AS task
                JOIN dongxiao_task_execution_items AS item
                  ON item.task_id=task.task_id
                 AND item.store_app_id=task.store_app_id
                WHERE task.store_app_id=?
                  AND task.task_type=?
                  AND item.subject_type=?
                  AND item.subject_id=?
                  AND task.status IN (
                      'pending', 'preparing', 'running', 'paused',
                      'unknown', 'completed'
                  )
                ORDER BY task.created_at DESC, task.task_id DESC
                LIMIT 1
                """,
                (
                    str(store_app_id),
                    str(task_type),
                    str(subject_type),
                    str(subject_id),
                ),
            ).fetchone()
        return self._task_from_row(row) if row else None

    def task_statistics(self, store_id: int) -> TaskStatistics:
        with self.connect() as conn:
            task_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM dongxiao_tasks
                WHERE store_id=?
                GROUP BY status
                """,
                (int(store_id),),
            ).fetchall()
            item_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_items,
                    SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END)
                        AS succeeded_items,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END)
                        AS failed_items,
                    SUM(CASE WHEN status='unknown' THEN 1 ELSE 0 END)
                        AS unknown_items
                FROM dongxiao_task_execution_items
                WHERE store_id=?
                """,
                (int(store_id),),
            ).fetchone()
        counts = {
            str(row["status"]): int(row["count"])
            for row in task_rows
        }
        return TaskStatistics(
            total=sum(counts.values()),
            pending=counts.get("pending", 0),
            running=(
                counts.get("preparing", 0)
                + counts.get("running", 0)
            ),
            paused=counts.get("paused", 0),
            completed=counts.get("completed", 0),
            failed=(
                counts.get("failed", 0)
                + counts.get("partially_failed", 0)
            ),
            unknown=counts.get("unknown", 0),
            cancelled=counts.get("cancelled", 0),
            execution_items=int(item_row["total_items"] or 0),
            succeeded_items=int(item_row["succeeded_items"] or 0),
            failed_items=int(item_row["failed_items"] or 0),
            unknown_items=int(item_row["unknown_items"] or 0),
            by_status=counts,
        )

    def record_api_call(self, call: ApiCallRecord) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO dongxiao_api_calls(
                    call_id, store_app_id, request_id, method, path,
                    attempt_no, outcome, errcode, rid, error_type,
                    error_message, started_at, finished_at, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call.call_id,
                    call.store_app_id,
                    call.request_id,
                    call.method,
                    call.path,
                    call.attempt_no,
                    call.outcome,
                    call.errcode,
                    call.rid,
                    call.error_type,
                    call.error_message,
                    to_utc_iso(call.started_at),
                    to_utc_iso(call.finished_at),
                    call.duration_ms,
                ),
            )

    def list_api_calls(
        self,
        store_app_id: str = "",
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if store_app_id:
                rows = conn.execute(
                    """
                    SELECT * FROM dongxiao_api_calls
                    WHERE store_app_id=?
                    ORDER BY started_at DESC, call_id DESC
                    LIMIT ?
                    """,
                    (str(store_app_id), max(1, min(10000, int(limit)))),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM dongxiao_api_calls
                    ORDER BY started_at DESC, call_id DESC
                    LIMIT ?
                    """,
                    (max(1, min(10000, int(limit))),),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_task_attempts(
        self,
        task_id: str,
        *,
        store_app_id: str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_execution_attempts
                WHERE task_id=? AND store_app_id=?
                ORDER BY started_at DESC, attempt_id DESC
                LIMIT ?
                """,
                (
                    str(task_id),
                    str(store_app_id),
                    max(1, min(10000, int(limit))),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_task_attempt(self, attempt: TaskAttempt) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, attempt.store_id, attempt.store_app_id)
            conn.execute(
                """
                INSERT INTO dongxiao_task_attempts(
                    attempt_id, task_id, item_id, store_id, store_app_id,
                    attempt_no, status, request_id, started_at, finished_at,
                    error_code, error_message, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.task_id,
                    attempt.item_id,
                    attempt.store_id,
                    attempt.store_app_id,
                    attempt.attempt_no,
                    attempt.status.value,
                    attempt.request_id,
                    to_utc_iso(attempt.started_at),
                    (
                        to_utc_iso(attempt.finished_at)
                        if attempt.finished_at is not None
                        else None
                    ),
                    attempt.error_code,
                    attempt.error_message,
                    _json(attempt.result),
                ),
            )

    def record_execution_attempt(self, attempt: TaskAttempt) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, attempt.store_id, attempt.store_app_id)
            conn.execute(
                """
                INSERT INTO dongxiao_execution_attempts(
                    attempt_id, task_id, item_id, store_id, store_app_id,
                    attempt_no, status, request_id, started_at, finished_at,
                    error_code, error_message, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.task_id,
                    attempt.item_id,
                    attempt.store_id,
                    attempt.store_app_id,
                    attempt.attempt_no,
                    attempt.status.value,
                    attempt.request_id,
                    to_utc_iso(attempt.started_at),
                    (
                        to_utc_iso(attempt.finished_at)
                        if attempt.finished_at is not None
                        else None
                    ),
                    attempt.error_code,
                    attempt.error_message,
                    _json(attempt.result),
                ),
            )

    def record_order_reference(self, order: RealOrderReference) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, order.store_id, order.store_app_id)
            now = to_utc_iso(datetime.now(UTC))
            conn.execute(
                """
                INSERT INTO dongxiao_order_refs(
                    order_ref_id, store_id, store_app_id, platform_order_id,
                    status, verified, source, verified_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_ref_id,
                    order.store_id,
                    order.store_app_id,
                    order.platform_order_id,
                    order.status.value,
                    1 if order.verified else 0,
                    order.source.value,
                    (
                        to_utc_iso(order.verified_at)
                        if order.verified_at is not None
                        else None
                    ),
                    to_utc_iso(order.created_at),
                    now,
                ),
            )

    def record_order_snapshot(self, snapshot: OrderStatusSnapshot) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, snapshot.store_id, snapshot.store_app_id)
            reference = conn.execute(
                """
                SELECT platform_order_id FROM dongxiao_order_refs
                WHERE order_ref_id=? AND store_app_id=?
                """,
                (snapshot.order_ref_id, snapshot.store_app_id),
            ).fetchone()
            if reference is None:
                raise EntityNotFound("真实订单引用不存在")
            if str(reference["platform_order_id"]) != snapshot.platform_order_id:
                raise StoreIsolationError("订单快照与订单引用不匹配")
            conn.execute(
                """
                INSERT INTO dongxiao_order_status_snapshots(
                    snapshot_id, order_ref_id, store_id, store_app_id,
                    platform_order_id, status, trusted, source,
                    request_id, details_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.order_ref_id,
                    snapshot.store_id,
                    snapshot.store_app_id,
                    snapshot.platform_order_id,
                    snapshot.status.value,
                    1 if snapshot.trusted else 0,
                    snapshot.source.value,
                    snapshot.request_id,
                    _json(snapshot.details),
                    to_utc_iso(snapshot.observed_at),
                ),
            )
            if snapshot.trusted:
                conn.execute(
                    """
                    UPDATE dongxiao_order_refs
                    SET status=?, verified=1, source='platform_api',
                        verified_at=?, updated_at=?
                    WHERE order_ref_id=? AND store_app_id=?
                    """,
                    (
                        snapshot.status.value,
                        to_utc_iso(snapshot.observed_at),
                        to_utc_iso(datetime.now(UTC)),
                        snapshot.order_ref_id,
                        snapshot.store_app_id,
                    ),
                )

    def upsert_order_record(
        self,
        order: OrderRecord,
        *,
        request_id: str = "",
    ) -> OrderRecord:
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_order_record_in_connection(
                conn,
                order,
                request_id=request_id,
                now=now,
            )
        saved = self.get_order_record(
            order.store_id,
            order.platform_order_id,
        )
        if saved is None:
            raise RepositoryError("订单保存后无法读取")
        return saved

    def upsert_order_records_batch(
        self,
        orders: list[OrderRecord],
    ) -> int:
        if not orders:
            return 0
        now = to_utc_iso(datetime.now(UTC))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for store_id, store_app_id in {
                (order.store_id, order.store_app_id) for order in orders
            }:
                self._ensure_store(conn, store_id, store_app_id)
            for order in orders:
                self._upsert_order_record_in_connection(
                    conn,
                    order,
                    request_id=f"order:{order.platform_order_id}",
                    now=now,
                )
        return len(orders)

    def _upsert_order_record_in_connection(
        self,
        conn: sqlite3.Connection,
        order: OrderRecord,
        *,
        request_id: str,
        now: str,
    ) -> str:
        self._ensure_store(conn, order.store_id, order.store_app_id)
        existing = conn.execute(
            """
            SELECT order_ref_id FROM dongxiao_order_refs
            WHERE store_app_id=? AND platform_order_id=?
            """,
            (order.store_app_id, order.platform_order_id),
        ).fetchone()
        order_ref_id = (
            str(existing["order_ref_id"])
            if existing is not None
            else order.order_ref_id
        )
        if existing is None:
            conn.execute(
                """
                INSERT INTO dongxiao_order_refs(
                    order_ref_id, store_id, store_app_id,
                    platform_order_id, status, verified, source,
                    verified_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, 'platform_api', ?, ?, ?)
                """,
                (
                    order_ref_id,
                    order.store_id,
                    order.store_app_id,
                    order.platform_order_id,
                    order.status.value,
                    now,
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE dongxiao_order_refs
                SET status=?, verified=1, source='platform_api',
                    verified_at=?, updated_at=?
                WHERE order_ref_id=? AND store_app_id=?
                """,
                (
                    order.status.value,
                    now,
                    now,
                    order_ref_id,
                    order.store_app_id,
                ),
            )
        conn.execute(
            """
            INSERT INTO dongxiao_order_status_snapshots(
                snapshot_id, order_ref_id, store_id, store_app_id,
                platform_order_id, status, trusted, source,
                request_id, details_json, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 'platform_api', ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                order_ref_id,
                order.store_id,
                order.store_app_id,
                order.platform_order_id,
                order.status.value,
                str(request_id)[:200],
                _json(
                    {
                        "item_count": order.item_count,
                        "total_amount_cents": order.total_amount_cents,
                    }
                ),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO dongxiao_order_details(
                order_ref_id, store_id, store_app_id, platform_order_id,
                status, create_time, update_time, pay_time,
                total_amount_cents, product_amount_cents,
                freight_amount_cents, discount_amount_cents,
                item_count, items_json, source, last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_ref_id, store_app_id) DO UPDATE SET
                store_id=excluded.store_id,
                platform_order_id=excluded.platform_order_id,
                status=excluded.status,
                create_time=excluded.create_time,
                update_time=excluded.update_time,
                pay_time=excluded.pay_time,
                total_amount_cents=excluded.total_amount_cents,
                product_amount_cents=excluded.product_amount_cents,
                freight_amount_cents=excluded.freight_amount_cents,
                discount_amount_cents=excluded.discount_amount_cents,
                item_count=excluded.item_count,
                items_json=excluded.items_json,
                source=excluded.source,
                last_synced_at=excluded.last_synced_at
            """,
            (
                order_ref_id,
                order.store_id,
                order.store_app_id,
                order.platform_order_id,
                order.status.value,
                (
                    to_utc_iso(order.create_time)
                    if order.create_time is not None
                    else None
                ),
                (
                    to_utc_iso(order.update_time)
                    if order.update_time is not None
                    else None
                ),
                (
                    to_utc_iso(order.pay_time)
                    if order.pay_time is not None
                    else None
                ),
                order.total_amount_cents,
                order.product_amount_cents,
                order.freight_amount_cents,
                order.discount_amount_cents,
                order.item_count,
                _json(list(order.items)),
                order.source.value,
                to_utc_iso(order.last_synced_at),
            ),
        )
        self._insert_audit(
            conn,
            store_id=order.store_id,
            store_app_id=order.store_app_id,
            actor_id="system",
            action="order_synced",
            entity_type="order",
            entity_id=order.platform_order_id,
            request_id=str(request_id),
            result=AuditResult.SUCCESS,
            detail={
                "status": order.status.value,
                "item_count": order.item_count,
                "total_amount_cents": order.total_amount_cents,
            },
        )
        return order_ref_id

    def get_order_record(
        self,
        store_id: int,
        platform_order_id: str,
    ) -> OrderRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dongxiao_order_details
                WHERE store_id=? AND platform_order_id=?
                """,
                (int(store_id), str(platform_order_id)),
            ).fetchone()
        return self._order_record_from_row(row) if row else None

    def list_order_records(
        self,
        store_id: int,
        *,
        limit: int = 500,
    ) -> list[OrderRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_order_details
                WHERE store_id=?
                ORDER BY
                    COALESCE(update_time, create_time, last_synced_at) DESC,
                    platform_order_id DESC
                LIMIT ?
                """,
                (int(store_id), max(1, min(10000, int(limit)))),
            ).fetchall()
        return [self._order_record_from_row(row) for row in rows]

    def list_order_records_by_store_app_id(
        self,
        store_app_id: str,
        *,
        limit: int = 500,
    ) -> list[OrderRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_order_details
                WHERE store_app_id=?
                ORDER BY
                    COALESCE(update_time, create_time, last_synced_at) DESC,
                    platform_order_id DESC
                LIMIT ?
                """,
                (str(store_app_id), max(1, min(10000, int(limit)))),
            ).fetchall()
        return [self._order_record_from_row(row) for row in rows]

    def list_order_snapshots(
        self,
        store_app_id: str,
        platform_order_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_order_status_snapshots
                WHERE store_app_id=? AND platform_order_id=?
                ORDER BY observed_at DESC, snapshot_id DESC
                LIMIT ?
                """,
                (
                    str(store_app_id),
                    str(platform_order_id),
                    max(1, min(1000, int(limit))),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_aftersale(self, case: AftersaleCase) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, case.store_id, case.store_app_id)
            conn.execute(
                """
                INSERT INTO dongxiao_aftersale_cases(
                    case_id, store_id, store_app_id, platform_case_id,
                    order_ref_id, status, requested_amount_cents,
                    approved_amount_cents, idempotency_key, source, reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case.case_id,
                    case.store_id,
                    case.store_app_id,
                    case.platform_case_id,
                    case.order_ref_id or None,
                    case.status.value,
                    case.requested_amount_cents,
                    case.approved_amount_cents,
                    case.idempotency_key,
                    case.source,
                    case.reason,
                    to_utc_iso(case.created_at),
                    to_utc_iso(case.updated_at),
                ),
            )

    def upsert_aftersale_case(self, case: AftersaleCase) -> AftersaleCase:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, case.store_id, case.store_app_id)
            previous = conn.execute(
                """
                SELECT status, platform_state, requested_amount_cents
                FROM dongxiao_aftersale_cases
                WHERE case_id=? AND store_app_id=?
                """,
                (case.case_id, case.store_app_id),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO dongxiao_aftersale_cases(
                    case_id, store_id, store_app_id, platform_case_id,
                    order_ref_id, status, requested_amount_cents,
                    approved_amount_cents, idempotency_key, source, reason,
                    created_at, updated_at, platform_status, after_sale_type,
                    paid_amount_cents, refunded_amount_cents,
                    inflight_amount_cents, deadline, complete_time,
                    details_json, last_synced_at, platform_state,
                    platform_state_updated_at, platform_reason
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(store_app_id, platform_case_id) DO UPDATE SET
                    order_ref_id=excluded.order_ref_id,
                    status=excluded.status,
                    requested_amount_cents=excluded.requested_amount_cents,
                    approved_amount_cents=excluded.approved_amount_cents,
                    source=excluded.source,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at,
                    platform_status=excluded.platform_status,
                    after_sale_type=excluded.after_sale_type,
                    paid_amount_cents=excluded.paid_amount_cents,
                    refunded_amount_cents=excluded.refunded_amount_cents,
                    inflight_amount_cents=excluded.inflight_amount_cents,
                    deadline=excluded.deadline,
                    complete_time=excluded.complete_time,
                    details_json=excluded.details_json,
                    last_synced_at=excluded.last_synced_at,
                    platform_state=excluded.platform_state,
                    platform_state_updated_at=excluded.platform_state_updated_at,
                    platform_reason=excluded.platform_reason
                """,
                (
                    case.case_id,
                    case.store_id,
                    case.store_app_id,
                    case.platform_case_id,
                    case.order_ref_id or None,
                    case.status.value,
                    case.requested_amount_cents,
                    case.approved_amount_cents,
                    case.idempotency_key,
                    case.source,
                    case.reason,
                    to_utc_iso(case.created_at),
                    to_utc_iso(case.updated_at),
                    case.platform_status,
                    case.after_sale_type,
                    case.paid_amount_cents,
                    case.refunded_amount_cents,
                    case.inflight_amount_cents,
                    (
                        to_utc_iso(case.deadline)
                        if case.deadline is not None
                        else None
                    ),
                    (
                        to_utc_iso(case.complete_time)
                        if case.complete_time is not None
                        else None
                    ),
                    _json(case.details),
                    (
                        to_utc_iso(case.last_synced_at)
                        if case.last_synced_at is not None
                        else None
                    ),
                    case.platform_state.value,
                    (
                        to_utc_iso(case.platform_state_updated_at)
                        if case.platform_state_updated_at is not None
                        else None
                    ),
                    case.platform_reason,
                ),
            )
            if (
                previous is None
                or (
                    str(previous["status"]) != case.status.value
                    or str(previous["platform_state"])
                    != case.platform_state.value
                )
            ):
                conn.execute(
                    """
                    INSERT INTO dongxiao_aftersale_status_history(
                        history_id, case_id, store_id, store_app_id,
                        previous_workflow_status, new_workflow_status,
                        previous_platform_state, new_platform_state,
                        platform_status, source, actor_id, reason, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        case.case_id,
                        case.store_id,
                        case.store_app_id,
                        (
                            str(previous["status"])
                            if previous is not None
                            else None
                        ),
                        case.status.value,
                        (
                            str(previous["platform_state"])
                            if previous is not None
                            else None
                        ),
                        case.platform_state.value,
                        case.platform_status,
                        case.source,
                        "system",
                        "",
                        to_utc_iso(datetime.now(UTC)),
                    ),
                )
            if (
                previous is not None
                and int(previous["requested_amount_cents"])
                != case.requested_amount_cents
                and str(previous["status"])
                in {"pending_review", "approved"}
            ):
                now = to_utc_iso(datetime.now(UTC))
                conn.execute(
                    """
                    UPDATE dongxiao_approvals
                    SET status='cancelled', decided_by='system',
                        decision_reason='官方售后金额变化，原审批自动失效',
                        decided_at=?
                    WHERE store_app_id=? AND subject_type='aftersale'
                      AND subject_id=?
                      AND status IN ('pending', 'approved')
                    """,
                    (now, case.store_app_id, case.case_id),
                )
                self._insert_audit(
                    conn,
                    store_id=case.store_id,
                    store_app_id=case.store_app_id,
                    actor_id="system",
                    action="aftersale_amount_changed",
                    entity_type="aftersale",
                    entity_id=case.platform_case_id,
                    request_id="",
                    result=AuditResult.INFO,
                    detail={
                        "previous_requested_amount_cents": int(
                            previous["requested_amount_cents"]
                        ),
                        "new_requested_amount_cents": (
                            case.requested_amount_cents
                        ),
                        "message": "原审批已失效，需要重新核验金额",
                    },
                )
            self._insert_audit(
                conn,
                store_id=case.store_id,
                store_app_id=case.store_app_id,
                actor_id="system",
                action="aftersale_synced",
                entity_type="aftersale",
                entity_id=case.platform_case_id,
                request_id="",
                result=AuditResult.SUCCESS,
                detail={
                    "status": case.status.value,
                    "platform_status": case.platform_status,
                    "requested_amount_cents": case.requested_amount_cents,
                },
            )
            if case.platform_state is AftersaleStatus.UNKNOWN:
                self._insert_audit(
                    conn,
                    store_id=case.store_id,
                    store_app_id=case.store_app_id,
                    actor_id="system",
                    action="aftersale_platform_status_unknown",
                    entity_type="aftersale",
                    entity_id=case.platform_case_id,
                    request_id="",
                    result=AuditResult.INFO,
                    detail={
                        "platform_status": case.platform_status,
                        "message": "官方售后状态未识别，审批和执行已阻止",
                    },
                )
        saved = self.get_aftersale_case(
            case.store_id,
            case.platform_case_id,
        )
        if saved is None:
            raise RepositoryError("售后工单保存后无法读取")
        return saved

    def get_aftersale_case(
        self,
        store_id: int,
        platform_case_id: str,
    ) -> AftersaleCase | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT cases.*,
                       refs.platform_order_id AS platform_order_id
                FROM dongxiao_aftersale_cases AS cases
                LEFT JOIN dongxiao_order_refs AS refs
                  ON refs.order_ref_id=cases.order_ref_id
                 AND refs.store_app_id=cases.store_app_id
                WHERE cases.store_id=?
                  AND (
                      cases.platform_case_id=? OR cases.case_id=?
                  )
                """,
                (
                    int(store_id),
                    str(platform_case_id),
                    str(platform_case_id),
                ),
            ).fetchone()
        return self._aftersale_from_row(row) if row else None

    def list_aftersale_cases(
        self,
        store_id: int,
        *,
        statuses: tuple[str, ...] = (),
        limit: int = 500,
    ) -> list[AftersaleCase]:
        sql = """
            SELECT cases.*,
                   refs.platform_order_id AS platform_order_id
            FROM dongxiao_aftersale_cases AS cases
            LEFT JOIN dongxiao_order_refs AS refs
              ON refs.order_ref_id=cases.order_ref_id
             AND refs.store_app_id=cases.store_app_id
            WHERE cases.store_id=?
        """
        args: list[Any] = [int(store_id)]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND cases.status IN ({placeholders})"
            args.extend(str(status) for status in statuses)
        sql += " ORDER BY cases.updated_at DESC, cases.case_id LIMIT ?"
        args.append(max(1, min(10000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._aftersale_from_row(row) for row in rows]

    def list_aftersale_cases_by_store_app_id(
        self,
        store_app_id: str,
        *,
        limit: int = 500,
    ) -> list[AftersaleCase]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT cases.*,
                       refs.platform_order_id AS platform_order_id
                FROM dongxiao_aftersale_cases AS cases
                LEFT JOIN dongxiao_order_refs AS refs
                  ON refs.order_ref_id=cases.order_ref_id
                 AND refs.store_app_id=cases.store_app_id
                WHERE cases.store_app_id=?
                ORDER BY cases.updated_at DESC, cases.case_id
                LIMIT ?
                """,
                (str(store_app_id), max(1, min(10000, int(limit)))),
            ).fetchall()
        return [self._aftersale_from_row(row) for row in rows]

    def list_aftersale_status_history(
        self,
        store_app_id: str,
        case_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_aftersale_status_history
                WHERE store_app_id=? AND case_id=?
                ORDER BY observed_at DESC, history_id DESC
                LIMIT ?
                """,
                (
                    str(store_app_id),
                    str(case_id),
                    max(1, min(10000, int(limit))),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def aftersale_refund_commitments(
        self,
        *,
        store_id: int,
        order_ref_id: str,
        exclude_case_id: str,
    ) -> tuple[int, int, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(refunded_amount_cents), 0),
                    COALESCE(SUM(inflight_amount_cents), 0),
                    COALESCE(SUM(
                        CASE
                            WHEN case_id<>?
                             AND status IN ('pending_review', 'approved')
                            THEN CASE
                                WHEN approved_amount_cents>0
                                THEN approved_amount_cents
                                ELSE requested_amount_cents
                            END
                            ELSE 0
                        END
                    ), 0)
                FROM dongxiao_aftersale_cases
                WHERE store_id=? AND order_ref_id=?
                """,
                (
                    str(exclude_case_id),
                    int(store_id),
                    str(order_ref_id),
                ),
            ).fetchone()
        return (
            int(row[0] or 0),
            int(row[1] or 0),
            int(row[2] or 0),
        )

    def transition_aftersale(
        self,
        case_id: str,
        *,
        store_id: int,
        store_app_id: str,
        event: str,
        actor_id: str = "",
        reason: str = "",
    ) -> AftersaleCase:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            row = conn.execute(
                """
                SELECT * FROM dongxiao_aftersale_cases
                WHERE case_id=? AND store_app_id=?
                """,
                (str(case_id), str(store_app_id)),
            ).fetchone()
            if row is None:
                raise EntityNotFound("售后工单不存在")
            ensure_same_store(store_id, int(row["store_id"]), label="售后工单")
            target = transition_aftersale(str(row["status"]), event)
            now = to_utc_iso(datetime.now(UTC))
            conn.execute(
                """
                UPDATE dongxiao_aftersale_cases
                SET status=?, reason=?, updated_at=?
                WHERE case_id=? AND store_app_id=? AND status=?
                """,
                (
                    target.value,
                    str(reason or row["reason"])[:1000],
                    now,
                    str(case_id),
                    str(store_app_id),
                    str(row["status"]),
                ),
            )
            conn.execute(
                """
                INSERT INTO dongxiao_aftersale_status_history(
                    history_id, case_id, store_id, store_app_id,
                    previous_workflow_status, new_workflow_status,
                    previous_platform_state, new_platform_state,
                    platform_status, source, actor_id, reason, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    str(case_id),
                    int(store_id),
                    str(store_app_id),
                    str(row["status"]),
                    target.value,
                    str(row["platform_state"] or "unknown"),
                    str(row["platform_state"] or "unknown"),
                    str(row["platform_status"] or ""),
                    str(actor_id),
                    str(reason),
                    now,
                ),
            )
            self._insert_audit(
                conn,
                store_id=store_id,
                store_app_id=store_app_id,
                actor_id=actor_id,
                action="aftersale_status_changed",
                entity_type="aftersale",
                entity_id=str(case_id),
                request_id="",
                result=AuditResult.SUCCESS,
                detail={
                    "from": str(row["status"]),
                    "event": str(event),
                    "to": target.value,
                    "reason": reason,
                },
            )
            saved = conn.execute(
                """
                SELECT * FROM dongxiao_aftersale_cases
                WHERE case_id=? AND store_app_id=?
                """,
                (str(case_id), str(store_app_id)),
            ).fetchone()
        if saved is None:
            raise RepositoryError("售后工单状态更新后无法读取")
        return self._aftersale_from_row(saved)

    def create_approval(self, approval: Approval) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(
                conn,
                approval.store_id,
                approval.store_app_id,
            )
            conn.execute(
                """
                INSERT INTO dongxiao_approvals(
                    approval_id, store_id, store_app_id, subject_type,
                    subject_id, status, requested_by, requested_at,
                    decided_by, decision_reason, decided_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.store_id,
                    approval.store_app_id,
                    approval.subject_type.value,
                    approval.subject_id,
                    approval.status.value,
                    approval.requested_by,
                    to_utc_iso(approval.requested_at),
                    approval.decided_by,
                    approval.decision_reason,
                    (
                        to_utc_iso(approval.decided_at)
                        if approval.decided_at is not None
                        else None
                    ),
                    (
                        to_utc_iso(approval.expires_at)
                        if approval.expires_at is not None
                        else None
                    ),
                ),
            )

    def transition_approval(
        self,
        approval_id: str,
        *,
        store_id: int,
        store_app_id: str,
        event: str,
        actor_id: str,
        reason: str = "",
    ) -> Approval:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, store_id, store_app_id)
            row = conn.execute(
                """
                SELECT * FROM dongxiao_approvals
                WHERE approval_id=? AND store_app_id=?
                """,
                (str(approval_id), str(store_app_id)),
            ).fetchone()
            if row is None:
                raise EntityNotFound("审批不存在")
            target = transition_approval(str(row["status"]), event)
            now = to_utc_iso(datetime.now(UTC))
            conn.execute(
                """
                UPDATE dongxiao_approvals
                SET status=?, decided_by=?, decision_reason=?,
                    decided_at=?
                WHERE approval_id=? AND store_app_id=? AND status=?
                """,
                (
                    target.value,
                    str(actor_id)[:200],
                    str(reason)[:1000],
                    now,
                    str(approval_id),
                    str(store_app_id),
                    str(row["status"]),
                ),
            )
            self._insert_audit(
                conn,
                store_id=store_id,
                store_app_id=store_app_id,
                actor_id=actor_id,
                action="approval_decided",
                entity_type="approval",
                entity_id=str(approval_id),
                request_id="",
                result=AuditResult.SUCCESS,
                detail={
                    "from": str(row["status"]),
                    "event": str(event),
                    "to": target.value,
                    "reason": reason,
                },
            )
            saved = conn.execute(
                """
                SELECT * FROM dongxiao_approvals
                WHERE approval_id=? AND store_app_id=?
                """,
                (str(approval_id), str(store_app_id)),
            ).fetchone()
        if saved is None:
            raise RepositoryError("审批状态更新后无法读取")
        return self._approval_from_row(saved)

    def get_approval(
        self,
        approval_id: str,
        *,
        store_app_id: str,
    ) -> Approval | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dongxiao_approvals
                WHERE approval_id=? AND store_app_id=?
                """,
                (str(approval_id), str(store_app_id)),
            ).fetchone()
        return self._approval_from_row(row) if row else None

    def list_approvals(
        self,
        store_id: int,
        *,
        subject_type: str = "",
        subject_id: str = "",
        limit: int = 500,
    ) -> list[Approval]:
        sql = """
            SELECT * FROM dongxiao_approvals
            WHERE store_id=?
        """
        args: list[Any] = [int(store_id)]
        if subject_type:
            sql += " AND subject_type=?"
            args.append(str(subject_type))
        if subject_id:
            sql += " AND subject_id=?"
            args.append(str(subject_id))
        sql += " ORDER BY requested_at DESC, approval_id LIMIT ?"
        args.append(max(1, min(10000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def list_approvals_by_store_app_id(
        self,
        store_app_id: str,
        *,
        subject_id: str = "",
        limit: int = 500,
    ) -> list[Approval]:
        sql = """
            SELECT * FROM dongxiao_approvals
            WHERE store_app_id=?
        """
        args: list[Any] = [str(store_app_id)]
        if subject_id:
            sql += " AND subject_id=?"
            args.append(str(subject_id))
        sql += " ORDER BY requested_at DESC, approval_id LIMIT ?"
        args.append(max(1, min(10000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def append_audit_event(self, event: AuditEvent) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event.store_id is not None:
                self._ensure_store(conn, event.store_id, event.store_app_id)
            conn.execute(
                """
                INSERT INTO dongxiao_audit_events(
                    event_id, store_id, store_app_id, actor_id, action,
                    entity_type, entity_id, request_id, result,
                    detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.store_id,
                    event.store_app_id,
                    event.actor_id,
                    event.action,
                    event.entity_type,
                    event.entity_id,
                    event.request_id,
                    event.result.value,
                    _json(event.detail),
                    to_utc_iso(event.created_at),
                ),
            )

    def list_audit_events(
        self,
        *,
        store_app_id: str = "",
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if store_app_id:
                rows = conn.execute(
                    """
                    SELECT * FROM dongxiao_audit_events
                    WHERE store_app_id=?
                    ORDER BY created_at DESC, row_id DESC
                    """,
                    (str(store_app_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM dongxiao_audit_events
                    ORDER BY created_at DESC, row_id DESC
                    """
                ).fetchall()
        return [dict(row) for row in rows]

    def _insert_audit(
        self,
        conn: sqlite3.Connection,
        *,
        store_id: int | None,
        store_app_id: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        request_id: str,
        result: AuditResult,
        detail: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO dongxiao_audit_events(
                event_id, store_id, store_app_id, actor_id, action,
                entity_type, entity_id, request_id, result,
                detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                store_id,
                store_app_id,
                actor_id,
                action,
                entity_type,
                entity_id,
                request_id,
                result.value,
                _json(detail),
                to_utc_iso(datetime.now(UTC)),
            ),
        )

    @staticmethod
    def _append_pool_event(
        conn: sqlite3.Connection,
        *,
        pool_id: str,
        store_id: int,
        store_app_id: str,
        action: str,
        product_id: str = "",
        sku_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO dongxiao_product_pool_events(
                event_id, pool_id, store_id, store_app_id, action,
                product_id, sku_id, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                str(pool_id),
                int(store_id),
                str(store_app_id),
                str(action),
                str(product_id or ""),
                str(sku_id or ""),
                _json(detail or {}),
                to_utc_iso(datetime.now(UTC)),
            ),
        )

    @staticmethod
    def _get_task_in_connection(
        conn: sqlite3.Connection,
        task_id: str,
        store_app_id: str,
    ) -> Task | None:
        row = conn.execute(
            """
            SELECT * FROM dongxiao_tasks
            WHERE task_id=? AND store_app_id=?
            """,
            (task_id, store_app_id),
        ).fetchone()
        return DongxiaoRepository._task_from_row(row) if row else None

    @staticmethod
    def _product_from_row(row: sqlite3.Row) -> Product:
        return Product(
            store_id=int(row["store_id"]),
            store_app_id=str(row["store_app_id"]),
            product_id=str(row["product_id"]),
            title=str(row["title"]),
            state=ProductState(row["state"]),
            min_price_cents=int(row["min_price_cents"]),
            stock_total=int(row["stock_total"]),
            observed_at=parse_utc_iso(row["observed_at"]),
            sales_count=(
                int(row["sales_count"])
                if row["sales_count"] is not None
                else None
            ),
            sales_source=(
                DataSource(row["sales_source"])
                if row["sales_source"]
                else None
            ),
            listing_time=(
                parse_utc_iso(row["listing_time"])
                if row["listing_time"]
                else None
            ),
            listing_source=(
                DataSource(row["listing_source"])
                if row["listing_source"]
                else None
            ),
            data_source=DataSource(row["data_source"]),
            from_cache=bool(row["from_cache"]),
            last_synced_at=(
                parse_utc_iso(row["last_synced_at"])
                if row["last_synced_at"]
                else None
            ),
            presence_state=PresenceState(row["presence_state"]),
            missing_since=(
                parse_utc_iso(row["missing_since"])
                if row["missing_since"]
                else None
            ),
        )

    @staticmethod
    def _sku_from_row(row: sqlite3.Row) -> Sku:
        specs = tuple(
            (str(item[0]), str(item[1]))
            for item in json.loads(row["specs_json"] or "[]")
            if isinstance(item, list) and len(item) == 2
        )
        return Sku(
            store_id=int(row["store_id"]),
            store_app_id=str(row["store_app_id"]),
            product_id=str(row["product_id"]),
            sku_id=str(row["sku_id"]),
            specs=specs,
            price_cents=int(row["price_cents"]),
            stock=int(row["stock"]),
            available=bool(row["available"]),
            purchase_min=int(row["purchase_min"]),
            purchase_max=(
                int(row["purchase_max"])
                if row["purchase_max"] is not None
                else None
            ),
            is_default=bool(row["is_default"]),
            observed_at=parse_utc_iso(row["observed_at"]),
            data_complete=bool(row["data_complete"]),
            presence_state=PresenceState(row["presence_state"]),
            missing_since=(
                parse_utc_iso(row["missing_since"])
                if row["missing_since"]
                else None
            ),
            purchase_min_verified=bool(row["purchase_min_verified"]),
        )

    @staticmethod
    def _order_record_from_row(row: sqlite3.Row) -> OrderRecord:
        items = tuple(
            dict(item)
            for item in _load_list(row["items_json"])
            if isinstance(item, dict)
        )
        return OrderRecord(
            order_ref_id=str(row["order_ref_id"]),
            store_id=(
                int(row["store_id"])
                if row["store_id"] is not None
                else None
            ),
            store_app_id=str(row["store_app_id"]),
            platform_order_id=str(row["platform_order_id"]),
            status=OrderStatus(row["status"]),
            create_time=_row_time(row["create_time"]),
            update_time=_row_time(row["update_time"]),
            pay_time=_row_time(row["pay_time"]),
            total_amount_cents=int(row["total_amount_cents"]),
            product_amount_cents=int(row["product_amount_cents"]),
            freight_amount_cents=int(row["freight_amount_cents"]),
            discount_amount_cents=int(row["discount_amount_cents"]),
            item_count=int(row["item_count"]),
            items=items,
            source=OrderVerificationSource(row["source"]),
            last_synced_at=parse_utc_iso(row["last_synced_at"]),
        )

    @staticmethod
    def _aftersale_from_row(row: sqlite3.Row) -> AftersaleCase:
        return AftersaleCase(
            case_id=str(row["case_id"]),
            store_id=(
                int(row["store_id"])
                if row["store_id"] is not None
                else None
            ),
            store_app_id=str(row["store_app_id"]),
            platform_case_id=str(row["platform_case_id"]),
            order_ref_id=str(row["order_ref_id"] or ""),
            status=AftersaleStatus(row["status"]),
            requested_amount_cents=int(row["requested_amount_cents"]),
            approved_amount_cents=int(row["approved_amount_cents"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
            source=str(row["source"]),
            reason=str(row["reason"] or ""),
            platform_status=str(row["platform_status"] or ""),
            platform_reason=str(row["platform_reason"] or ""),
            platform_order_id=str(
                row["platform_order_id"]
                if "platform_order_id" in row.keys()
                and row["platform_order_id"]
                else ""
            ),
            platform_state=AftersaleStatus(
                row["platform_state"] or "unknown"
            ),
            platform_state_updated_at=_row_time(
                row["platform_state_updated_at"]
            ),
            after_sale_type=str(row["after_sale_type"] or ""),
            paid_amount_cents=int(row["paid_amount_cents"]),
            refunded_amount_cents=int(row["refunded_amount_cents"]),
            inflight_amount_cents=int(row["inflight_amount_cents"]),
            deadline=_row_time(row["deadline"]),
            complete_time=_row_time(row["complete_time"]),
            details=_load_json(row["details_json"]),
            last_synced_at=_row_time(row["last_synced_at"]),
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        return Approval(
            approval_id=str(row["approval_id"]),
            store_id=(
                int(row["store_id"])
                if row["store_id"] is not None
                else None
            ),
            store_app_id=str(row["store_app_id"]),
            subject_type=ApprovalSubjectType(row["subject_type"]),
            subject_id=str(row["subject_id"]),
            status=ApprovalStatus(row["status"]),
            requested_by=str(row["requested_by"]),
            requested_at=parse_utc_iso(row["requested_at"]),
            decided_by=str(row["decided_by"] or ""),
            decision_reason=str(row["decision_reason"] or ""),
            decided_at=_row_time(row["decided_at"]),
            expires_at=_row_time(row["expires_at"]),
        )

    @staticmethod
    def _pool_from_row(row: sqlite3.Row) -> ProductPool:
        return ProductPool(
            pool_id=str(row["pool_id"]),
            store_id=int(row["store_id"]),
            store_app_id=str(row["store_app_id"]),
            name=str(row["name"]),
            status=ProductPoolStatus(row["status"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
            group_name=str(row["group_name"] or ""),
            copied_from_pool_id=str(row["copied_from_pool_id"] or ""),
            version=int(row["version"] or 1),
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Task:
        return Task(
            task_id=str(row["task_id"]),
            store_id=(
                int(row["store_id"])
                if row["store_id"] is not None
                else None
            ),
            store_app_id=str(row["store_app_id"]),
            task_type=str(row["task_type"]),
            idempotency_key=str(row["idempotency_key"]),
            status=TaskStatus(row["status"]),
            total_items=int(row["total_items"]),
            succeeded_items=int(row["succeeded_items"]),
            failed_items=int(row["failed_items"]),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
            metadata=_load_json(row["metadata_json"]),
            status_reason=str(row["status_reason"] or ""),
            priority=int(row["priority"] or 0),
            max_attempts=int(row["max_attempts"] or 3),
            next_run_at=_row_time(row["next_run_at"]),
            lease_owner=str(row["lease_owner"] or ""),
            lease_expires_at=_row_time(row["lease_expires_at"]),
            last_heartbeat_at=_row_time(row["last_heartbeat_at"]),
            started_at=_row_time(row["started_at"]),
            finished_at=_row_time(row["finished_at"]),
            last_error=str(row["last_error"] or ""),
            execution_mode=str(row["execution_mode"] or "local_safe"),
        )

    @staticmethod
    def _execution_item_from_row(
        row: sqlite3.Row,
    ) -> TaskExecutionItem:
        return TaskExecutionItem(
            item_id=str(row["item_id"]),
            task_id=str(row["task_id"]),
            store_id=(
                int(row["store_id"])
                if row["store_id"] is not None
                else None
            ),
            store_app_id=str(row["store_app_id"]),
            subject_type=str(row["subject_type"]),
            subject_id=str(row["subject_id"]),
            product_id=str(row["product_id"] or ""),
            sku_id=str(row["sku_id"] or ""),
            status=TaskItemStatus(row["status"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            next_attempt_at=_row_time(row["next_attempt_at"]),
            started_at=_row_time(row["started_at"]),
            finished_at=_row_time(row["finished_at"]),
            last_error_code=str(row["last_error_code"] or ""),
            last_error_message=str(row["last_error_message"] or ""),
            payload=_load_json(row["payload_json"]),
            result=_load_json(row["result_json"]),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
        )
