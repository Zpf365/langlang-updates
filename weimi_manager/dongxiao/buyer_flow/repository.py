from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
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
    BuyerAccount,
    BuyerAccountStatus,
    BuyerAccountSwitchEvent,
    BuyerFlowItem,
    BuyerFlowMode,
    BuyerFlowRun,
    BuyerFlowStatus,
    BuyerIdentityStatus,
    BuyerItemStatus,
    BuyerOrderLink,
    BuyerOrderStatus,
    BuyerOrderVerificationStatus,
    BuyerProvider,
)


class BuyerFlowRepositoryError(RuntimeError):
    pass


class BuyerFlowIdempotencyConflict(BuyerFlowRepositoryError):
    pass


def _json(value: object) -> str:
    return json.dumps(
        sanitize_audit_detail(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_dict(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_specs(value: str | None) -> tuple[tuple[str, str], ...]:
    payload = _load_dict(value)
    return tuple((str(k), str(v)) for k, v in sorted(payload.items()))


def _time(value: str | None) -> datetime | None:
    return parse_utc_iso(value) if value else None


class BuyerFlowRepository:
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

    def create_account(self, account: BuyerAccount) -> tuple[BuyerAccount, bool]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=? OR account_ref_hash=?
                """,
                (account.buyer_account_id, account.account_ref_hash),
            ).fetchone()
            if existing is not None:
                return self._account_from_row(existing), False
            current = conn.execute(
                """
                SELECT 1
                  FROM dongxiao_buyer_accounts
                 WHERE is_current=1
                 LIMIT 1
                """
            ).fetchone()
            is_current = bool(account.is_current and current is None)
            conn.execute(
                """
                INSERT INTO dongxiao_buyer_accounts(
                    buyer_account_id, display_name, account_ref_hash,
                    provider, identity_status, status, is_current,
                    authorization_at, last_verified_at, status_reason,
                    created_at, updated_at, wechat_nickname,
                    identity_label, remark, last_used_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account.buyer_account_id,
                    account.display_name,
                    account.account_ref_hash,
                    account.provider.value,
                    account.identity_status.value,
                    account.status.value,
                    1 if is_current else 0,
                    (
                        to_utc_iso(account.authorization_at)
                        if account.authorization_at
                        else None
                    ),
                    (
                        to_utc_iso(account.last_verified_at)
                        if account.last_verified_at
                        else None
                    ),
                    account.status_reason,
                    to_utc_iso(account.created_at),
                    to_utc_iso(account.updated_at),
                    account.wechat_nickname,
                    account.identity_label,
                    account.remark,
                    (
                        to_utc_iso(account.last_used_at)
                        if account.last_used_at
                        else None
                    ),
                ),
            )
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (account.buyer_account_id,),
            ).fetchone()
        return self._account_from_row(row), True

    def list_accounts(self) -> list[BuyerAccount]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_accounts
                 ORDER BY is_current DESC, created_at, buyer_account_id
                """
            ).fetchall()
        return [self._account_from_row(row) for row in rows]

    def get_account(self, buyer_account_id: str) -> BuyerAccount | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (str(buyer_account_id),),
            ).fetchone()
        return self._account_from_row(row) if row is not None else None

    def current_account(self) -> BuyerAccount | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_accounts
                 WHERE is_current=1
                 LIMIT 1
                """
            ).fetchone()
        return self._account_from_row(row) if row is not None else None

    def update_account(
        self,
        buyer_account_id: str,
        *,
        display_name: str | None = None,
        wechat_nickname: str | None = None,
        identity_label: str | None = None,
        remark: str | None = None,
        identity_status: BuyerIdentityStatus | str | None = None,
        status: BuyerAccountStatus | str | None = None,
        authorization_at: datetime | None = None,
        last_verified_at: datetime | None = None,
        status_reason: str | None = None,
    ) -> BuyerAccount:
        clauses = ["updated_at=?"]
        values: list[object] = [to_utc_iso(datetime.now(UTC))]
        for name, value in (
            ("display_name", display_name),
            ("wechat_nickname", wechat_nickname),
            ("identity_label", identity_label),
            ("remark", remark),
            (
                "identity_status",
                BuyerIdentityStatus(identity_status).value
                if identity_status is not None
                else None,
            ),
            (
                "status",
                BuyerAccountStatus(status).value if status is not None else None,
            ),
            (
                "authorization_at",
                to_utc_iso(authorization_at) if authorization_at else None,
            ),
            (
                "last_verified_at",
                to_utc_iso(last_verified_at) if last_verified_at else None,
            ),
            ("status_reason", status_reason),
        ):
            if value is not None:
                clauses.append(f"{name}=?")
                values.append(value)
        values.append(str(buyer_account_id))
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE dongxiao_buyer_accounts SET "
                + ", ".join(clauses)
                + " WHERE buyer_account_id=?",
                values,
            )
            if cursor.rowcount != 1:
                raise BuyerFlowRepositoryError("买家账号不存在")
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (str(buyer_account_id),),
            ).fetchone()
        return self._account_from_row(row)

    def set_current_account(
        self,
        buyer_account_id: str,
        *,
        actor_id: str = "",
        reason: str = "",
    ) -> BuyerAccount:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                """
                SELECT buyer_account_id
                  FROM dongxiao_buyer_accounts
                 WHERE is_current=1
                 LIMIT 1
                """
            ).fetchone()
            account = conn.execute(
                """
                SELECT identity_status, status, provider
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (str(buyer_account_id),),
            ).fetchone()
            if account is None:
                raise BuyerFlowRepositoryError("买家账号不存在")
            current_id = (
                str(previous["buyer_account_id"])
                if previous is not None
                else ""
            )
            if current_id and current_id != str(buyer_account_id):
                active = conn.execute(
                    """
                    SELECT 1
                      FROM dongxiao_buyer_flow_runs
                     WHERE buyer_account_id=?
                       AND status IN (
                           'prepared', 'waiting_user_handoff',
                           'waiting_order_id', 'paused', 'unknown'
                       )
                     LIMIT 1
                    """,
                    (current_id,),
                ).fetchone()
                if active is not None:
                    raise BuyerFlowRepositoryError(
                        "当前买家账号存在未完成任务，禁止切换账号"
                    )
            verified = account["identity_status"] in {
                BuyerIdentityStatus.VERIFIED_OFFICIAL.value,
                BuyerIdentityStatus.VERIFIED_MOCK.value,
            }
            manual_handoff = (
                account["provider"] == BuyerProvider.MANUAL_HANDOFF.value
                and account["identity_status"]
                == BuyerIdentityStatus.USER_ASSERTED.value
            )
            if not verified and not manual_handoff:
                raise BuyerFlowRepositoryError("买家身份未经官方验证，不能设为当前账号")
            conn.execute(
                "UPDATE dongxiao_buyer_accounts SET is_current=0 WHERE is_current=1"
            )
            now = to_utc_iso(datetime.now(UTC))
            conn.execute(
                """
                UPDATE dongxiao_buyer_accounts
                   SET is_current=1, last_used_at=?, updated_at=?
                 WHERE buyer_account_id=?
                """,
                (now, now, str(buyer_account_id)),
            )
            if str(previous["buyer_account_id"] if previous else "") != str(
                buyer_account_id
            ):
                conn.execute(
                    """
                    INSERT INTO dongxiao_buyer_account_switch_events(
                        switch_id, from_buyer_account_id,
                        to_buyer_account_id, actor_id, reason, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        (
                            str(previous["buyer_account_id"])
                            if previous is not None
                            else None
                        ),
                        str(buyer_account_id),
                        str(actor_id or "system"),
                        str(reason or "")[:500],
                        now,
                    ),
                )
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (str(buyer_account_id),),
            ).fetchone()
        return self._account_from_row(row)

    def list_account_switch_events(
        self,
        *,
        limit: int = 200,
    ) -> list[BuyerAccountSwitchEvent]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_account_switch_events
                 ORDER BY created_at DESC, rowid DESC
                 LIMIT ?
                """,
                (max(1, min(10_000, int(limit))),),
            ).fetchall()
        return [
            BuyerAccountSwitchEvent(
                switch_id=str(row["switch_id"]),
                from_buyer_account_id=str(
                    row["from_buyer_account_id"] or ""
                ),
                to_buyer_account_id=str(row["to_buyer_account_id"]),
                actor_id=str(row["actor_id"]),
                reason=str(row["reason"] or ""),
                created_at=parse_utc_iso(row["created_at"]),
            )
            for row in rows
        ]

    def record_client_event(
        self,
        *,
        buyer_account_id: str,
        client_state: str,
        window_title: str = "",
        process_id: int | None = None,
        detail: dict[str, object] | None = None,
        screenshot_path: str = "",
    ) -> dict[str, Any]:
        created_at = to_utc_iso(datetime.now(UTC))
        event_id = uuid.uuid4().hex
        with self.connect() as conn:
            if buyer_account_id and conn.execute(
                """
                SELECT 1 FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (str(buyer_account_id),),
            ).fetchone() is None:
                raise BuyerFlowRepositoryError("买家账号不存在")
            conn.execute(
                """
                INSERT INTO dongxiao_buyer_client_events(
                    event_id, buyer_account_id, client_state,
                    window_title, process_id, detail_json,
                    screenshot_path, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(buyer_account_id) or None,
                    str(client_state),
                    str(window_title or "")[:500],
                    int(process_id) if process_id else None,
                    _json(detail or {}),
                    str(screenshot_path or "")[:2000],
                    created_at,
                ),
            )
        return {
            "event_id": event_id,
            "buyer_account_id": str(buyer_account_id),
            "client_state": str(client_state),
            "window_title": str(window_title or ""),
            "process_id": int(process_id) if process_id else None,
            "detail": dict(detail or {}),
            "screenshot_path": str(screenshot_path or ""),
            "created_at": created_at,
        }

    def list_client_events(
        self,
        *,
        buyer_account_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[object] = []
        if buyer_account_id:
            clauses.append("buyer_account_id=?")
            args.append(str(buyer_account_id))
        sql = "SELECT * FROM dongxiao_buyer_client_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(max(1, min(10_000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "buyer_account_id": str(row["buyer_account_id"] or ""),
                "client_state": str(row["client_state"]),
                "window_title": str(row["window_title"] or ""),
                "process_id": (
                    int(row["process_id"])
                    if row["process_id"] is not None
                    else None
                ),
                "detail": _load_dict(row["detail_json"]),
                "screenshot_path": str(row["screenshot_path"] or ""),
                "created_at": parse_utc_iso(row["created_at"]),
            }
            for row in rows
        ]

    def record_task_event(
        self,
        *,
        run_id: str,
        buyer_account_id: str,
        event_type: str,
        status: str,
        detail: dict[str, object] | None = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO dongxiao_buyer_task_events(
                    event_id, run_id, buyer_account_id,
                    event_type, status, detail_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(run_id),
                    str(buyer_account_id),
                    str(event_type),
                    str(status),
                    _json(detail or {}),
                    to_utc_iso(datetime.now(UTC)),
                ),
            )
        return event_id

    def list_task_events(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_task_events
                 WHERE run_id=?
                 ORDER BY created_at, rowid
                """,
                (str(run_id),),
            ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "run_id": str(row["run_id"]),
                "buyer_account_id": str(row["buyer_account_id"]),
                "event_type": str(row["event_type"]),
                "status": str(row["status"]),
                "detail": _load_dict(row["detail_json"]),
                "created_at": parse_utc_iso(row["created_at"]),
            }
            for row in rows
        ]

    def delete_account(self, buyer_account_id: str) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT is_current
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (str(buyer_account_id),),
            ).fetchone()
            if row is None:
                raise BuyerFlowRepositoryError("买家账号不存在")
            if int(row["is_current"] or 0):
                raise BuyerFlowRepositoryError("请先切换当前买家账号后再删除")
            active = conn.execute(
                """
                SELECT 1
                  FROM dongxiao_buyer_flow_runs
                 WHERE buyer_account_id=?
                   AND status IN (
                       'prepared', 'waiting_user_handoff',
                       'waiting_order_id', 'paused', 'unknown'
                   )
                 LIMIT 1
                """,
                (str(buyer_account_id),),
            ).fetchone()
            if active is not None:
                raise BuyerFlowRepositoryError("买家账号存在未完成流程，不能删除")
            conn.execute(
                "DELETE FROM dongxiao_buyer_accounts WHERE buyer_account_id=?",
                (str(buyer_account_id),),
            )

    def create_run(
        self,
        run: BuyerFlowRun,
        items: list[BuyerFlowItem],
    ) -> tuple[BuyerFlowRun, bool]:
        if len(items) != run.item_count:
            raise ValueError("买家流程明细数量与流程记录不一致")
        if any(item.run_id != run.run_id for item in items):
            raise ValueError("买家流程明细不属于当前流程")
        fingerprint = hashlib.sha256(
            _json(
                {
                    "store_app_id": run.store_app_id,
                    "buyer_account_id": run.buyer_account_id,
                    "mode": run.mode.value,
                    "items": [
                        (
                            item.product_id,
                            item.sku_id,
                            item.quantity,
                            item.unit_price_cents,
                        )
                        for item in items
                    ],
                }
            ).encode("utf-8")
        ).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if run.store_id is not None:
                self._ensure_store(conn, run.store_id, run.store_app_id)
            account = conn.execute(
                """
                SELECT 1
                  FROM dongxiao_buyer_accounts
                 WHERE buyer_account_id=?
                """,
                (run.buyer_account_id,),
            ).fetchone()
            if account is None:
                raise BuyerFlowRepositoryError("买家账号不存在")
            existing = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_flow_runs
                 WHERE store_app_id=? AND idempotency_key=?
                """,
                (run.store_app_id, run.idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["request_fingerprint"]) != fingerprint:
                    raise BuyerFlowIdempotencyConflict(
                        "相同幂等键已用于不同买家流程"
                    )
                return self._run_from_row(existing), False
            conn.execute(
                """
                INSERT INTO dongxiao_buyer_flow_runs(
                    run_id, store_id, store_app_id, store_name,
                    buyer_account_id, mode, status, idempotency_key,
                    request_fingerprint, requested_count, item_count,
                    merchandise_total_cents, expires_at, handoff_token,
                    order_id, verification_status, status_reason,
                    capability_json, created_at, updated_at
                ) VALUES(
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    run.run_id,
                    run.store_id,
                    run.store_app_id,
                    run.store_name,
                    run.buyer_account_id,
                    run.mode.value,
                    run.status.value,
                    run.idempotency_key,
                    fingerprint,
                    run.requested_count,
                    run.item_count,
                    run.merchandise_total_cents,
                    to_utc_iso(run.expires_at) if run.expires_at else None,
                    run.handoff_token,
                    run.order_id,
                    run.verification_status.value,
                    run.status_reason,
                    _json(dict(run.capability)),
                    to_utc_iso(run.created_at),
                    to_utc_iso(run.updated_at),
                ),
            )
            conn.executemany(
                """
                INSERT INTO dongxiao_buyer_flow_items(
                    item_id, run_id, product_id, title, sku_id,
                    specs_json, unit_price_cents, quantity, stock,
                    status, reason
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.item_id,
                        item.run_id,
                        item.product_id,
                        item.title,
                        item.sku_id,
                        _json(dict(item.specs)),
                        item.unit_price_cents,
                        item.quantity,
                        item.stock,
                        item.status.value,
                        item.reason,
                    )
                    for item in items
                ],
            )
            row = conn.execute(
                "SELECT * FROM dongxiao_buyer_flow_runs WHERE run_id=?",
                (run.run_id,),
            ).fetchone()
        return self._run_from_row(row), True

    def get_run(self, run_id: str) -> BuyerFlowRun | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dongxiao_buyer_flow_runs WHERE run_id=?",
                (str(run_id),),
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(
        self,
        *,
        store_id: int | None = None,
        buyer_account_id: str = "",
        limit: int = 500,
    ) -> list[BuyerFlowRun]:
        clauses = []
        args: list[object] = []
        if store_id is not None:
            clauses.append("store_id=?")
            args.append(int(store_id))
        if buyer_account_id:
            clauses.append("buyer_account_id=?")
            args.append(str(buyer_account_id))
        sql = "SELECT * FROM dongxiao_buyer_flow_runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, run_id LIMIT ?"
        args.append(max(1, min(10_000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._run_from_row(row) for row in rows]

    def list_items(self, run_id: str) -> list[BuyerFlowItem]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_flow_items
                 WHERE run_id=?
                 ORDER BY item_id
                """,
                (str(run_id),),
            ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def update_run(
        self,
        run_id: str,
        *,
        status: BuyerFlowStatus | str | None = None,
        handoff_token: str | None = None,
        order_id: str | None = None,
        verification_status: BuyerOrderVerificationStatus | str | None = None,
        order_status: BuyerOrderStatus | str | None = None,
        status_reason: str | None = None,
    ) -> BuyerFlowRun:
        clauses = ["updated_at=?"]
        values: list[object] = [to_utc_iso(datetime.now(UTC))]
        for name, value in (
            ("status", BuyerFlowStatus(status).value if status else None),
            ("handoff_token", handoff_token),
            ("order_id", order_id),
            (
                "verification_status",
                (
                    BuyerOrderVerificationStatus(verification_status).value
                    if verification_status
                    else None
                ),
            ),
            (
                "order_status",
                BuyerOrderStatus(order_status).value
                if order_status is not None
                else None,
            ),
            ("status_reason", status_reason),
        ):
            if value is not None:
                clauses.append(f"{name}=?")
                values.append(value)
        values.append(str(run_id))
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE dongxiao_buyer_flow_runs SET "
                + ", ".join(clauses)
                + " WHERE run_id=?",
                values,
            )
            if cursor.rowcount != 1:
                raise BuyerFlowRepositoryError("买家流程不存在")
            row = conn.execute(
                "SELECT * FROM dongxiao_buyer_flow_runs WHERE run_id=?",
                (str(run_id),),
            ).fetchone()
        return self._run_from_row(row)

    def record_order_link(self, link: BuyerOrderLink) -> BuyerOrderLink:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO dongxiao_buyer_order_links(
                    link_id, run_id, store_id, store_app_id,
                    buyer_account_id, platform_order_id, status,
                    total_amount_cents, item_count, detail_json,
                    created_at, verified_at, order_status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, platform_order_id) DO UPDATE SET
                    status=excluded.status,
                    total_amount_cents=excluded.total_amount_cents,
                    item_count=excluded.item_count,
                    detail_json=excluded.detail_json,
                    verified_at=excluded.verified_at,
                    order_status=excluded.order_status
                """,
                (
                    link.link_id,
                    link.run_id,
                    link.store_id,
                    link.store_app_id,
                    link.buyer_account_id,
                    link.platform_order_id,
                    link.status.value,
                    link.total_amount_cents,
                    link.item_count,
                    _json(dict(link.detail)),
                    to_utc_iso(link.created_at),
                    to_utc_iso(link.verified_at),
                    link.order_status.value,
                ),
            )
            row = conn.execute(
                """
                SELECT *
                  FROM dongxiao_buyer_order_links
                 WHERE run_id=? AND platform_order_id=?
                """,
                (link.run_id, link.platform_order_id),
            ).fetchone()
        return self._link_from_row(row)

    def list_order_links(
        self,
        *,
        store_id: int | None = None,
        buyer_account_id: str = "",
        limit: int = 500,
    ) -> list[BuyerOrderLink]:
        clauses = []
        args: list[object] = []
        if store_id is not None:
            clauses.append("store_id=?")
            args.append(int(store_id))
        if buyer_account_id:
            clauses.append("buyer_account_id=?")
            args.append(str(buyer_account_id))
        sql = "SELECT * FROM dongxiao_buyer_order_links"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY verified_at DESC LIMIT ?"
        args.append(max(1, min(10_000, int(limit))))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._link_from_row(row) for row in rows]

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> BuyerAccount:
        return BuyerAccount(
            buyer_account_id=str(row["buyer_account_id"]),
            display_name=str(row["display_name"]),
            account_ref_hash=str(row["account_ref_hash"]),
            provider=BuyerProvider(row["provider"]),
            identity_status=BuyerIdentityStatus(row["identity_status"]),
            status=BuyerAccountStatus(row["status"]),
            is_current=bool(row["is_current"]),
            authorization_at=_time(row["authorization_at"]),
            last_verified_at=_time(row["last_verified_at"]),
            status_reason=str(row["status_reason"] or ""),
            wechat_nickname=str(row["wechat_nickname"] or ""),
            identity_label=str(row["identity_label"] or ""),
            remark=str(row["remark"] or ""),
            last_used_at=_time(row["last_used_at"]),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> BuyerFlowRun:
        return BuyerFlowRun(
            run_id=str(row["run_id"]),
            store_id=int(row["store_id"]) if row["store_id"] is not None else None,
            store_app_id=str(row["store_app_id"]),
            store_name=str(row["store_name"]),
            buyer_account_id=str(row["buyer_account_id"]),
            mode=BuyerFlowMode(row["mode"]),
            status=BuyerFlowStatus(row["status"]),
            idempotency_key=str(row["idempotency_key"]),
            requested_count=int(row["requested_count"]),
            item_count=int(row["item_count"]),
            merchandise_total_cents=int(row["merchandise_total_cents"]),
            expires_at=_time(row["expires_at"]),
            handoff_token=str(row["handoff_token"] or ""),
            order_id=str(row["order_id"] or ""),
            verification_status=BuyerOrderVerificationStatus(
                row["verification_status"]
            ),
            status_reason=str(row["status_reason"] or ""),
            capability=_load_dict(row["capability_json"]),
            order_status=BuyerOrderStatus(
                row["order_status"] or BuyerOrderStatus.UNKNOWN.value
            ),
            created_at=parse_utc_iso(row["created_at"]),
            updated_at=parse_utc_iso(row["updated_at"]),
        )

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> BuyerFlowItem:
        return BuyerFlowItem(
            item_id=str(row["item_id"]),
            run_id=str(row["run_id"]),
            product_id=str(row["product_id"]),
            title=str(row["title"]),
            sku_id=str(row["sku_id"]),
            specs=_load_specs(row["specs_json"]),
            unit_price_cents=int(row["unit_price_cents"]),
            quantity=int(row["quantity"]),
            stock=int(row["stock"]),
            status=BuyerItemStatus(row["status"]),
            reason=str(row["reason"] or ""),
        )

    @staticmethod
    def _link_from_row(row: sqlite3.Row) -> BuyerOrderLink:
        return BuyerOrderLink(
            link_id=str(row["link_id"]),
            run_id=str(row["run_id"]),
            store_id=int(row["store_id"]) if row["store_id"] is not None else None,
            store_app_id=str(row["store_app_id"]),
            buyer_account_id=str(row["buyer_account_id"]),
            platform_order_id=str(row["platform_order_id"]),
            status=BuyerOrderVerificationStatus(row["status"]),
            total_amount_cents=int(row["total_amount_cents"]),
            item_count=int(row["item_count"]),
            detail=_load_dict(row["detail_json"]),
            order_status=BuyerOrderStatus(
                row["order_status"] or BuyerOrderStatus.UNKNOWN.value
            ),
            created_at=parse_utc_iso(row["created_at"]),
            verified_at=parse_utc_iso(row["verified_at"]),
        )
