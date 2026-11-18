from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from ..domain.enums import (
    AuditResult,
    ProductPoolStatus,
    ProductState,
    TaskEvent,
    TaskStatus,
)
from ..domain.models import (
    AftersaleCase,
    Approval,
    AuditEvent,
    OrderStatusSnapshot,
    Product,
    ProductPool,
    ProductPoolItem,
    ProductSnapshot,
    RealOrderReference,
    Sku,
    Task,
    TaskAttempt,
    TaskItem,
)
from ..domain.rules import (
    StoreIsolationError,
    ensure_same_store,
    parse_utc_iso,
    sanitize_audit_detail,
    task_request_fingerprint,
    to_utc_iso,
)
from ..domain.state_machine import transition_task


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
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_app_id, product_id) DO UPDATE SET
                    store_id=excluded.store_id,
                    title=excluded.title,
                    state=excluded.state,
                    min_price_cents=excluded.min_price_cents,
                    stock_total=excluded.stock_total,
                    observed_at=excluded.observed_at,
                    updated_at=excluded.updated_at
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
                    is_default, observed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    updated_at=excluded.updated_at
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
                ),
            )
        saved = self.get_sku(sku.store_id, sku.product_id, sku.sku_id)
        if saved is None:
            raise RepositoryError("SKU 保存后无法读取")
        return saved

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

    def list_skus(self, store_id: int, product_id: str) -> list[Sku]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_skus
                WHERE store_id=? AND product_id=?
                ORDER BY price_cents, sku_id
                """,
                (int(store_id), str(product_id)),
            ).fetchall()
        return [self._sku_from_row(row) for row in rows]

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
                    idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
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

    def create_task(self, task: Task) -> tuple[Task, bool]:
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
                return saved, False
            conn.execute(
                """
                INSERT INTO dongxiao_tasks(
                    task_id, store_id, store_app_id, task_type,
                    idempotency_key, request_fingerprint, status,
                    total_items, succeeded_items, failed_items,
                    status_reason, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
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
                    _json(task.metadata),
                    to_utc_iso(task.created_at),
                    to_utc_iso(task.updated_at),
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
        return task, True

    def get_task(self, task_id: str, *, store_app_id: str) -> Task | None:
        with self.connect() as conn:
            return self._get_task_in_connection(
                conn,
                task_id,
                store_app_id,
            )

    def list_tasks(self, store_id: int) -> list[Task]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dongxiao_tasks
                WHERE store_id=?
                ORDER BY created_at DESC, task_id
                """,
                (int(store_id),),
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
            target = transition_task(str(row["status"]), event)
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
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Task:
        return Task(
            task_id=str(row["task_id"]),
            store_id=int(row["store_id"]),
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
        )
