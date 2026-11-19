from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from ..domain.rules import (
    StoreIsolationError,
    parse_utc_iso,
    sanitize_audit_detail,
    to_utc_iso,
)
from .domain import (
    CartAccount,
    CartAccountStatus,
    CartAdapterMode,
    CartBatch,
    CartBatchStatus,
    CartResult,
    CartResultStatus,
    CartServerSnapshot,
    CartSkuMode,
    CartSpeedMode,
    OutOfStockPolicy,
    PendingOrderDraft,
    PendingOrderStatus,
)


class DesktopCartRepositoryError(RuntimeError):
    pass


class DesktopCartIdempotencyConflict(DesktopCartRepositoryError):
    pass


def _json(value: object) -> str:
    return json.dumps(
        sanitize_audit_detail(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_json(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _time(value: str | None) -> datetime | None:
    return parse_utc_iso(value) if value else None


class DesktopCartRepository:
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
            raise StoreIsolationError("店铺不存在")
        if str(row["app_id"]) != str(store_app_id):
            raise StoreIsolationError("店铺 ID 与 AppID 不一致")

    def create_account(self, account: CartAccount) -> tuple[CartAccount, bool]:
        if account.store_id is None:
            raise StoreIsolationError("执行账号必须绑定店铺")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(
                conn,
                account.store_id,
                account.store_app_id,
            )
            existing = conn.execute(
                """
                SELECT *
                FROM dongxiao_cart_accounts
                WHERE account_id=?
                   OR (store_app_id=? AND display_name=?)
                """,
                (
                    account.account_id,
                    account.store_app_id,
                    account.display_name,
                ),
            ).fetchone()
            if existing is not None:
                return self._account_from_row(existing), False
            conn.execute(
                """
                INSERT INTO dongxiao_cart_accounts(
                    account_id, store_id, store_app_id, display_name,
                    adapter_mode, status, authorization_ref,
                    last_connected_at, last_checked_at, last_error,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account.account_id,
                    account.store_id,
                    account.store_app_id,
                    account.display_name,
                    account.adapter_mode.value,
                    account.status.value,
                    account.authorization_ref,
                    (
                        to_utc_iso(account.last_connected_at)
                        if account.last_connected_at
                        else None
                    ),
                    (
                        to_utc_iso(account.last_checked_at)
                        if account.last_checked_at
                        else None
                    ),
                    account.last_error,
                    to_utc_iso(account.created_at),
                    to_utc_iso(account.updated_at),
                ),
            )
        return account, True

    def get_account(
        self,
        account_id: str,
        *,
        store_id: int | None = None,
        store_app_id: str | None = None,
    ) -> CartAccount | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dongxiao_cart_accounts WHERE account_id=?",
                (str(account_id),),
            ).fetchone()
        if row is None:
            return None
        account = self._account_from_row(row)
        if store_id is not None and account.store_id != int(store_id):
            raise StoreIsolationError("执行账号不属于当前店铺")
        if store_app_id is not None and account.store_app_id != str(store_app_id):
            raise StoreIsolationError("执行账号与当前店铺不一致")
        return account

    def list_accounts(
        self,
        *,
        store_id: int | None = None,
        include_deleted_store: bool = False,
    ) -> list[CartAccount]:
        sql = "SELECT * FROM dongxiao_cart_accounts"
        args: tuple[object, ...] = ()
        if store_id is not None:
            sql += " WHERE store_id=?"
            args = (int(store_id),)
        elif not include_deleted_store:
            sql += " WHERE store_id IS NOT NULL"
        sql += " ORDER BY created_at, account_id"
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._account_from_row(row) for row in rows]

    def update_account_status(
        self,
        account_id: str,
        status: CartAccountStatus,
        *,
        error: str = "",
        checked_at: datetime | None = None,
    ) -> CartAccount:
        now = checked_at or datetime.now(UTC)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE dongxiao_cart_accounts
                   SET status=?, last_error=?, last_checked_at=?,
                       last_connected_at=CASE
                           WHEN ?='connected' THEN ?
                           ELSE last_connected_at
                       END,
                       updated_at=?
                 WHERE account_id=?
                """,
                (
                    CartAccountStatus(status).value,
                    str(error or "")[:1000],
                    to_utc_iso(now),
                    CartAccountStatus(status).value,
                    to_utc_iso(now),
                    to_utc_iso(now),
                    str(account_id),
                ),
            )
            if cursor.rowcount != 1:
                raise DesktopCartRepositoryError("执行账号不存在")
            row = conn.execute(
                "SELECT * FROM dongxiao_cart_accounts WHERE account_id=?",
                (str(account_id),),
            ).fetchone()
        return self._account_from_row(row)

    def create_batch(
        self,
        batch: CartBatch,
        *,
        account_ids: list[str],
    ) -> tuple[CartBatch, bool]:
        if batch.store_id is None:
            raise StoreIsolationError("加购批次必须绑定店铺")
        normalized_accounts = tuple(dict.fromkeys(str(item) for item in account_ids if item))
        if not normalized_accounts:
            raise ValueError("加购批次至少需要一个执行账号")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store(conn, batch.store_id, batch.store_app_id)
            existing = conn.execute(
                """
                SELECT *
                FROM dongxiao_cart_batches
                WHERE store_app_id=? AND idempotency_key=?
                """,
                (batch.store_app_id, batch.idempotency_key),
            ).fetchone()
            if existing is not None:
                signature = (
                    str(existing["pool_id"]),
                    int(existing["total_products"]),
                    int(existing["quantity_per_product"]),
                    str(existing["sku_mode"]),
                    str(existing["specified_sku_id"]),
                )
                requested = (
                    batch.pool_id,
                    batch.total_products,
                    batch.quantity_per_product,
                    batch.sku_mode.value,
                    batch.specified_sku_id,
                )
                if signature != requested:
                    raise DesktopCartIdempotencyConflict(
                        "相同幂等键已用于不同加购参数"
                    )
                return self._batch_from_row(existing), False
            for account_id in normalized_accounts:
                account_row = conn.execute(
                    """
                    SELECT store_id, store_app_id
                    FROM dongxiao_cart_accounts
                    WHERE account_id=?
                    """,
                    (account_id,),
                ).fetchone()
                if account_row is None:
                    raise ValueError(f"执行账号不存在：{account_id}")
                if (
                    int(account_row["store_id"] or 0) != batch.store_id
                    or str(account_row["store_app_id"]) != batch.store_app_id
                ):
                    raise StoreIsolationError(
                        f"执行账号 {account_id} 与批次店铺不一致"
                    )
            conn.execute(
                """
                INSERT INTO dongxiao_cart_batches(
                    batch_id, store_id, store_app_id, pool_id, name,
                    idempotency_key, status, total_products,
                    products_per_batch, quantity_per_product, sku_mode,
                    specified_sku_id, out_of_stock_policy, speed_mode,
                    interval_ms, concurrency, adapter_mode, created_by,
                    created_at, updated_at, confirmed_at, started_at,
                    finished_at, last_error, summary_json
                ) VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    batch.batch_id,
                    batch.store_id,
                    batch.store_app_id,
                    batch.pool_id,
                    batch.name,
                    batch.idempotency_key,
                    batch.status.value,
                    batch.total_products,
                    batch.products_per_batch,
                    batch.quantity_per_product,
                    batch.sku_mode.value,
                    batch.specified_sku_id,
                    batch.out_of_stock_policy.value,
                    batch.speed_mode.value,
                    batch.interval_ms,
                    batch.concurrency,
                    batch.adapter_mode.value,
                    batch.created_by,
                    to_utc_iso(batch.created_at),
                    to_utc_iso(batch.updated_at),
                    (
                        to_utc_iso(batch.confirmed_at)
                        if batch.confirmed_at
                        else None
                    ),
                    to_utc_iso(batch.started_at) if batch.started_at else None,
                    to_utc_iso(batch.finished_at) if batch.finished_at else None,
                    batch.last_error,
                    _json(batch.summary),
                ),
            )
            for index, account_id in enumerate(normalized_accounts, 1):
                conn.execute(
                    """
                    INSERT INTO dongxiao_cart_assignments(
                        assignment_id, batch_id, account_id, sequence_no,
                        task_id, status, total_items, succeeded_items,
                        failed_items, skipped_items, unknown_items,
                        started_at, finished_at, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, '', 'pending', ?, 0, 0, 0, 0, NULL, NULL, ?, ?)
                    """,
                    (
                        f"{batch.batch_id}:{index:04d}",
                        batch.batch_id,
                        account_id,
                        index,
                        batch.total_products,
                        to_utc_iso(batch.created_at),
                        to_utc_iso(batch.updated_at),
                    ),
                )
        return batch, True

    def get_batch(
        self,
        batch_id: str,
        *,
        store_id: int | None = None,
        store_app_id: str | None = None,
    ) -> CartBatch | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dongxiao_cart_batches WHERE batch_id=?",
                (str(batch_id),),
            ).fetchone()
        if row is None:
            return None
        batch = self._batch_from_row(row)
        if store_id is not None and batch.store_id != int(store_id):
            raise StoreIsolationError("加购批次不属于当前店铺")
        if store_app_id is not None and batch.store_app_id != str(store_app_id):
            raise StoreIsolationError("加购批次与当前店铺不一致")
        return batch

    def list_batches(
        self,
        *,
        store_id: int | None = None,
        include_deleted_store: bool = False,
        limit: int = 500,
    ) -> list[CartBatch]:
        sql = "SELECT * FROM dongxiao_cart_batches"
        args: list[object] = []
        if store_id is not None:
            sql += " WHERE store_id=?"
            args.append(int(store_id))
        elif not include_deleted_store:
            sql += " WHERE store_id IS NOT NULL"
        sql += " ORDER BY created_at DESC, batch_id LIMIT ?"
        args.append(max(1, min(10_000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._batch_from_row(row) for row in rows]

    def list_assignments(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.*, c.display_name, c.status AS account_status,
                       c.adapter_mode
                  FROM dongxiao_cart_assignments a
                  JOIN dongxiao_cart_accounts c
                    ON c.account_id=a.account_id
                 WHERE a.batch_id=?
                 ORDER BY a.sequence_no, a.assignment_id
                """,
                (str(batch_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def bind_assignment_task(
        self,
        batch_id: str,
        account_id: str,
        task_id: str,
        *,
        now: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT task_id
                  FROM dongxiao_cart_assignments
                 WHERE batch_id=? AND account_id=?
                """,
                (str(batch_id), str(account_id)),
            ).fetchone()
            if row is None:
                raise DesktopCartRepositoryError("加购任务分配不存在")
            existing = str(row["task_id"] or "")
            if existing and existing != str(task_id):
                raise DesktopCartRepositoryError("账号任务已经绑定，禁止覆盖")
            conn.execute(
                """
                UPDATE dongxiao_cart_assignments
                   SET task_id=?, status='pending', updated_at=?
                 WHERE batch_id=? AND account_id=?
                """,
                (
                    str(task_id),
                    to_utc_iso(now),
                    str(batch_id),
                    str(account_id),
                ),
            )

    def update_batch(
        self,
        batch_id: str,
        *,
        status: CartBatchStatus | str | None = None,
        confirmed_at: datetime | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
        summary: dict[str, object] | None = None,
    ) -> CartBatch:
        values: list[object] = []
        clauses: list[str] = []
        for name, value in (
            ("status", CartBatchStatus(status).value if status is not None else None),
            ("confirmed_at", to_utc_iso(confirmed_at) if confirmed_at else None),
            ("started_at", to_utc_iso(started_at) if started_at else None),
            ("finished_at", to_utc_iso(finished_at) if finished_at else None),
            ("last_error", error),
            ("summary_json", _json(summary) if summary is not None else None),
        ):
            if value is not None:
                clauses.append(f"{name}=?")
                values.append(value)
        if not clauses:
            saved = self.get_batch(batch_id)
            if saved is None:
                raise DesktopCartRepositoryError("加购批次不存在")
            return saved
        clauses.append("updated_at=?")
        values.append(to_utc_iso(datetime.now(UTC)))
        values.append(str(batch_id))
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE dongxiao_cart_batches SET "
                + ", ".join(clauses)
                + " WHERE batch_id=?",
                values,
            )
            if cursor.rowcount != 1:
                raise DesktopCartRepositoryError("加购批次不存在")
        saved = self.get_batch(batch_id)
        if saved is None:
            raise DesktopCartRepositoryError("加购批次无法读取")
        return saved

    def sync_assignment_progress(
        self,
        *,
        batch_id: str,
        account_id: str,
        status: str,
        total_items: int,
        succeeded_items: int,
        failed_items: int,
        skipped_items: int,
        unknown_items: int,
        started_at: datetime | None,
        finished_at: datetime | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE dongxiao_cart_assignments
                   SET status=?, total_items=?, succeeded_items=?,
                       failed_items=?, skipped_items=?, unknown_items=?,
                       started_at=COALESCE(started_at, ?),
                       finished_at=?, updated_at=?
                 WHERE batch_id=? AND account_id=?
                """,
                (
                    str(status),
                    int(total_items),
                    int(succeeded_items),
                    int(failed_items),
                    int(skipped_items),
                    int(unknown_items),
                    to_utc_iso(started_at) if started_at else None,
                    to_utc_iso(finished_at) if finished_at else None,
                    to_utc_iso(datetime.now(UTC)),
                    str(batch_id),
                    str(account_id),
                ),
            )

    def record_result(self, result: CartResult) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_store_or_history(
                conn,
                result.store_id,
                result.store_app_id,
            )
            conn.execute(
                """
                INSERT INTO dongxiao_cart_results(
                    result_id, batch_id, account_id, task_id, item_id,
                    store_id, store_app_id, store_name, product_id,
                    product_title, sku_id, specs_json, unit_price_cents,
                    quantity, batch_no, status, started_at, finished_at,
                    failure_code, failure_reason, request_id,
                    verification_source, verification_token
                ) VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                ON CONFLICT(batch_id, account_id, product_id, sku_id)
                DO UPDATE SET
                    task_id=excluded.task_id,
                    item_id=excluded.item_id,
                    store_id=excluded.store_id,
                    store_name=excluded.store_name,
                    product_title=excluded.product_title,
                    specs_json=excluded.specs_json,
                    unit_price_cents=excluded.unit_price_cents,
                    quantity=excluded.quantity,
                    batch_no=excluded.batch_no,
                    status=excluded.status,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    failure_code=excluded.failure_code,
                    failure_reason=excluded.failure_reason,
                    request_id=excluded.request_id,
                    verification_source=excluded.verification_source,
                    verification_token=excluded.verification_token
                """,
                (
                    result.result_id,
                    result.batch_id,
                    result.account_id,
                    result.task_id,
                    result.item_id,
                    result.store_id,
                    result.store_app_id,
                    result.store_name,
                    result.product_id,
                    result.product_title,
                    result.sku_id,
                    _json(dict(result.specs)),
                    result.unit_price_cents,
                    result.quantity,
                    result.batch_no,
                    result.status.value,
                    to_utc_iso(result.started_at),
                    (
                        to_utc_iso(result.finished_at)
                        if result.finished_at
                        else None
                    ),
                    result.failure_code,
                    result.failure_reason,
                    result.request_id,
                    result.verification_source.value,
                    result.verification_token,
                ),
            )

    def record_verification(
        self,
        *,
        verification_id: str,
        result_id: str,
        snapshot: CartServerSnapshot,
        verified: bool,
        reason: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO dongxiao_cart_verifications(
                    verification_id, result_id, store_app_id, account_id,
                    product_id, sku_id, quantity, unit_price_cents,
                    source, server_state_token, raw_fingerprint,
                    verified, reason, observed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(verification_id),
                    str(result_id),
                    snapshot.store_app_id,
                    snapshot.account_id,
                    snapshot.product_id,
                    snapshot.sku_id,
                    snapshot.quantity,
                    snapshot.unit_price_cents,
                    snapshot.source.value,
                    snapshot.server_state_token,
                    snapshot.raw_fingerprint,
                    1 if verified else 0,
                    str(reason or "")[:1000],
                    to_utc_iso(snapshot.observed_at),
                ),
            )

    def list_results(
        self,
        *,
        batch_id: str = "",
        store_id: int | None = None,
        account_id: str = "",
        status: CartResultStatus | str | None = None,
        limit: int = 1000,
    ) -> list[CartResult]:
        clauses: list[str] = []
        args: list[object] = []
        if batch_id:
            clauses.append("batch_id=?")
            args.append(str(batch_id))
        if store_id is not None:
            clauses.append("store_id=?")
            args.append(int(store_id))
        if account_id:
            clauses.append("account_id=?")
            args.append(str(account_id))
        if status is not None:
            clauses.append("status=?")
            args.append(CartResultStatus(status).value)
        sql = "SELECT * FROM dongxiao_cart_results"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC, result_id LIMIT ?"
        args.append(max(1, min(100_000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._result_from_row(row) for row in rows]

    def statistics(self, *, store_id: int | None = None) -> dict[str, int]:
        where = " WHERE store_id=?" if store_id is not None else ""
        args: tuple[object, ...] = (
            (int(store_id),) if store_id is not None else ()
        )
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                  FROM dongxiao_cart_results
                """
                + where
                + " GROUP BY status",
                args,
            ).fetchall()
            batches = conn.execute(
                "SELECT COUNT(*) FROM dongxiao_cart_batches"
                + where,
                args,
            ).fetchone()[0]
            accounts = conn.execute(
                "SELECT COUNT(*) FROM dongxiao_cart_accounts"
                + where,
                args,
            ).fetchone()[0]
        output = {
            "batches": int(batches),
            "accounts": int(accounts),
            "pending": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "unknown": 0,
        }
        for row in rows:
            output[str(row["status"])] = int(row["count"])
        return output

    def upsert_mock_cart_state(
        self,
        snapshot: CartServerSnapshot,
    ) -> CartServerSnapshot:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO dongxiao_cart_mock_state(
                    store_app_id, account_id, product_id, sku_id,
                    quantity, unit_price_cents, server_state_token,
                    raw_fingerprint, observed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_app_id, account_id, product_id, sku_id)
                DO UPDATE SET
                    quantity=excluded.quantity,
                    unit_price_cents=excluded.unit_price_cents,
                    server_state_token=excluded.server_state_token,
                    raw_fingerprint=excluded.raw_fingerprint,
                    observed_at=excluded.observed_at
                """,
                (
                    snapshot.store_app_id,
                    snapshot.account_id,
                    snapshot.product_id,
                    snapshot.sku_id,
                    snapshot.quantity,
                    snapshot.unit_price_cents,
                    snapshot.server_state_token,
                    snapshot.raw_fingerprint,
                    to_utc_iso(snapshot.observed_at),
                ),
            )
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_cart_mock_state
                 WHERE store_app_id=? AND account_id=?
                   AND product_id=? AND sku_id=?
                """,
                (
                    snapshot.store_app_id,
                    snapshot.account_id,
                    snapshot.product_id,
                    snapshot.sku_id,
                ),
            ).fetchone()
        return self._snapshot_from_row(row)

    def apply_mock_cart_operation(
        self,
        *,
        operation_key: str,
        store_app_id: str,
        account_id: str,
        product_id: str,
        sku_id: str,
        quantity: int,
        unit_price_cents: int,
        now: datetime,
    ) -> tuple[CartServerSnapshot, str, bool]:
        if not str(operation_key or "").strip():
            raise ValueError("模拟加购幂等键不能为空")
        if quantity <= 0:
            raise ValueError("模拟加购数量必须为正整数")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_operation = conn.execute(
                """
                SELECT request_id
                  FROM dongxiao_cart_mock_operations
                 WHERE operation_key=?
                """,
                (str(operation_key),),
            ).fetchone()
            if existing_operation is not None:
                row = conn.execute(
                    """
                    SELECT *
                      FROM dongxiao_cart_mock_state
                     WHERE store_app_id=? AND account_id=?
                       AND product_id=? AND sku_id=?
                    """,
                    (store_app_id, account_id, product_id, sku_id),
                ).fetchone()
                if row is None:
                    raise DesktopCartRepositoryError(
                        "模拟幂等记录存在但购物车状态缺失"
                    )
                return (
                    self._snapshot_from_row(row),
                    str(existing_operation["request_id"]),
                    True,
                )
            current = conn.execute(
                """
                SELECT *
                  FROM dongxiao_cart_mock_state
                 WHERE store_app_id=? AND account_id=?
                   AND product_id=? AND sku_id=?
                """,
                (store_app_id, account_id, product_id, sku_id),
            ).fetchone()
            current_quantity = int(current["quantity"]) if current else 0
            request_id = f"mock-{operation_key}"
            state_token = (
                f"mock-state:{store_app_id}:{account_id}:{product_id}:{sku_id}:"
                f"{current_quantity + quantity}:{to_utc_iso(now)}"
            )
            conn.execute(
                """
                INSERT INTO dongxiao_cart_mock_operations(
                    operation_key, request_id, store_app_id, account_id,
                    product_id, sku_id, quantity, completed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(operation_key),
                    request_id,
                    store_app_id,
                    account_id,
                    product_id,
                    sku_id,
                    quantity,
                    to_utc_iso(now),
                ),
            )
            conn.execute(
                """
                INSERT INTO dongxiao_cart_mock_state(
                    store_app_id, account_id, product_id, sku_id,
                    quantity, unit_price_cents, server_state_token,
                    raw_fingerprint, observed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_app_id, account_id, product_id, sku_id)
                DO UPDATE SET
                    quantity=dongxiao_cart_mock_state.quantity + excluded.quantity,
                    unit_price_cents=excluded.unit_price_cents,
                    server_state_token=excluded.server_state_token,
                    raw_fingerprint=excluded.raw_fingerprint,
                    observed_at=excluded.observed_at
                """,
                (
                    store_app_id,
                    account_id,
                    product_id,
                    sku_id,
                    quantity,
                    unit_price_cents,
                    state_token,
                    f"operation:{operation_key}",
                    to_utc_iso(now),
                ),
            )
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_cart_mock_state
                 WHERE store_app_id=? AND account_id=?
                   AND product_id=? AND sku_id=?
                """,
                (store_app_id, account_id, product_id, sku_id),
            ).fetchone()
        return self._snapshot_from_row(row), request_id, False

    def get_mock_cart_state(
        self,
        *,
        store_app_id: str,
        account_id: str,
        product_id: str,
        sku_id: str,
    ) -> CartServerSnapshot | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_cart_mock_state
                 WHERE store_app_id=? AND account_id=?
                   AND product_id=? AND sku_id=?
                """,
                (
                    str(store_app_id),
                    str(account_id),
                    str(product_id),
                    str(sku_id),
                ),
            ).fetchone()
        return self._snapshot_from_row(row) if row is not None else None

    def create_pending_draft(
        self,
        draft: PendingOrderDraft,
    ) -> tuple[PendingOrderDraft, bool]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT *
                  FROM dongxiao_pending_order_drafts
                 WHERE batch_id=? AND account_id=?
                """,
                (draft.batch_id, draft.account_id),
            ).fetchone()
            if existing is not None:
                return self._draft_from_row(existing), False
            conn.execute(
                """
                INSERT INTO dongxiao_pending_order_drafts(
                    draft_id, batch_id, store_id, store_app_id,
                    store_name, account_id, status, item_count,
                    total_amount_cents, approval_id, decided_by,
                    decided_at, official_order_id, reason,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.draft_id,
                    draft.batch_id,
                    draft.store_id,
                    draft.store_app_id,
                    draft.store_name,
                    draft.account_id,
                    draft.status.value,
                    draft.item_count,
                    draft.total_amount_cents,
                    draft.approval_id,
                    draft.decided_by,
                    to_utc_iso(draft.decided_at) if draft.decided_at else None,
                    draft.official_order_id,
                    draft.reason,
                    to_utc_iso(draft.created_at),
                    to_utc_iso(draft.updated_at),
                ),
            )
        return draft, True

    def list_pending_drafts(
        self,
        *,
        store_id: int | None = None,
        batch_id: str = "",
        limit: int = 500,
    ) -> list[PendingOrderDraft]:
        clauses: list[str] = []
        args: list[object] = []
        if store_id is not None:
            clauses.append("store_id=?")
            args.append(int(store_id))
        if batch_id:
            clauses.append("batch_id=?")
            args.append(str(batch_id))
        sql = "SELECT * FROM dongxiao_pending_order_drafts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(10_000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._draft_from_row(row) for row in rows]

    def update_pending_draft(
        self,
        draft_id: str,
        *,
        status: PendingOrderStatus | str,
        decided_by: str = "",
        reason: str = "",
    ) -> PendingOrderDraft:
        now = datetime.now(UTC)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE dongxiao_pending_order_drafts
                   SET status=?, decided_by=?, decided_at=?,
                       reason=?, updated_at=?
                 WHERE draft_id=?
                """,
                (
                    PendingOrderStatus(status).value,
                    str(decided_by or "")[:200],
                    to_utc_iso(now),
                    str(reason or "")[:1000],
                    to_utc_iso(now),
                    str(draft_id),
                ),
            )
            if cursor.rowcount != 1:
                raise DesktopCartRepositoryError("待付款草案不存在")
            row = conn.execute(
                "SELECT * FROM dongxiao_pending_order_drafts WHERE draft_id=?",
                (str(draft_id),),
            ).fetchone()
        return self._draft_from_row(row)

    @staticmethod
    def _ensure_store_or_history(
        conn: sqlite3.Connection,
        store_id: int | None,
        store_app_id: str,
    ) -> None:
        if store_id is None:
            if not str(store_app_id or "").strip():
                raise StoreIsolationError("历史购物车记录必须保留店铺 AppID")
            return
        DesktopCartRepository._ensure_store(conn, store_id, store_app_id)

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> CartAccount:
        return CartAccount(
            account_id=str(row["account_id"]),
            store_id=int(row["store_id"]) if row["store_id"] is not None else None,
            store_app_id=str(row["store_app_id"]),
            display_name=str(row["display_name"]),
            adapter_mode=CartAdapterMode(row["adapter_mode"]),
            status=CartAccountStatus(row["status"]),
            authorization_ref=str(row["authorization_ref"] or ""),
            last_connected_at=_time(row["last_connected_at"]),
            last_checked_at=_time(row["last_checked_at"]),
            last_error=str(row["last_error"] or ""),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
        )

    @staticmethod
    def _batch_from_row(row: sqlite3.Row) -> CartBatch:
        return CartBatch(
            batch_id=str(row["batch_id"]),
            store_id=int(row["store_id"]) if row["store_id"] is not None else None,
            store_app_id=str(row["store_app_id"]),
            pool_id=str(row["pool_id"]),
            name=str(row["name"]),
            idempotency_key=str(row["idempotency_key"]),
            status=CartBatchStatus(row["status"]),
            total_products=int(row["total_products"]),
            products_per_batch=int(row["products_per_batch"]),
            quantity_per_product=int(row["quantity_per_product"]),
            sku_mode=CartSkuMode(row["sku_mode"]),
            specified_sku_id=str(row["specified_sku_id"] or ""),
            out_of_stock_policy=OutOfStockPolicy(row["out_of_stock_policy"]),
            speed_mode=CartSpeedMode(row["speed_mode"]),
            interval_ms=int(row["interval_ms"]),
            concurrency=int(row["concurrency"]),
            adapter_mode=CartAdapterMode(row["adapter_mode"]),
            created_by=str(row["created_by"] or ""),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
            confirmed_at=_time(row["confirmed_at"]),
            started_at=_time(row["started_at"]),
            finished_at=_time(row["finished_at"]),
            last_error=str(row["last_error"] or ""),
            summary=_load_json(row["summary_json"], {}),
        )

    @staticmethod
    def _result_from_row(row: sqlite3.Row) -> CartResult:
        specs = _load_json(row["specs_json"], {})
        return CartResult(
            result_id=str(row["result_id"]),
            batch_id=str(row["batch_id"]),
            account_id=str(row["account_id"]),
            task_id=str(row["task_id"]),
            item_id=str(row["item_id"]),
            store_id=int(row["store_id"]) if row["store_id"] is not None else None,
            store_app_id=str(row["store_app_id"]),
            store_name=str(row["store_name"]),
            product_id=str(row["product_id"]),
            product_title=str(row["product_title"]),
            sku_id=str(row["sku_id"]),
            specs=tuple((str(k), str(v)) for k, v in specs.items()),
            unit_price_cents=int(row["unit_price_cents"]),
            quantity=int(row["quantity"]),
            batch_no=int(row["batch_no"]),
            status=CartResultStatus(row["status"]),
            started_at=parse_utc_iso(row["started_at"]),
            finished_at=_time(row["finished_at"]),
            failure_code=str(row["failure_code"] or ""),
            failure_reason=str(row["failure_reason"] or ""),
            request_id=str(row["request_id"] or ""),
            verification_source=CartAdapterMode(row["verification_source"]),
            verification_token=str(row["verification_token"] or ""),
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> CartServerSnapshot:
        return CartServerSnapshot(
            store_app_id=str(row["store_app_id"]),
            account_id=str(row["account_id"]),
            product_id=str(row["product_id"]),
            sku_id=str(row["sku_id"]),
            quantity=int(row["quantity"]),
            unit_price_cents=int(row["unit_price_cents"]),
            server_state_token=str(row["server_state_token"]),
            raw_fingerprint=str(row["raw_fingerprint"] or ""),
            observed_at=parse_utc_iso(row["observed_at"]),
            source=CartAdapterMode.MOCK,
        )

    @staticmethod
    def _draft_from_row(row: sqlite3.Row) -> PendingOrderDraft:
        return PendingOrderDraft(
            draft_id=str(row["draft_id"]),
            batch_id=str(row["batch_id"]),
            store_id=int(row["store_id"]) if row["store_id"] is not None else None,
            store_app_id=str(row["store_app_id"]),
            store_name=str(row["store_name"]),
            account_id=str(row["account_id"]),
            status=PendingOrderStatus(row["status"]),
            item_count=int(row["item_count"]),
            total_amount_cents=int(row["total_amount_cents"]),
            approval_id=str(row["approval_id"] or ""),
            decided_by=str(row["decided_by"] or ""),
            decided_at=_time(row["decided_at"]),
            official_order_id=str(row["official_order_id"] or ""),
            reason=str(row["reason"] or ""),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
        )
