from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

from .constants import TRUSTED_ORDER_VERIFICATION_SOURCES
from .models import SkuCandidate
from .state_machine import SalesEvent, SalesTaskStatus, transition_task


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SalesRelatedRepository:
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

    def append_audit(
        self,
        conn: sqlite3.Connection,
        action: str,
        *,
        actor: str = "",
        task_id: str = "",
        device_id: str = "",
        decision: str = "",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO sales_audit_logs(
                action, actor, task_id, device_id, decision,
                detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                actor,
                task_id,
                device_id,
                decision,
                _json(dict(detail or {})),
                _now(),
            ),
        )

    def create_task(
        self,
        *,
        source_url: str,
        canonical_url: str,
        store_id: int = 0,
        store_app_id: str = "",
        device_id: str = "",
        buyer_ref: str = "",
        sku_selection_mode: str,
        specified_sku_id: str = "",
        specified_specs: Mapping[str, str] | None = None,
        purchase_quantity: int = 1,
        price_cap_cents: int = 0,
        created_by: str = "",
        simulation_mode: bool = True,
        task_id: str = "",
    ) -> dict[str, Any]:
        normalized_task_id = str(task_id or uuid.uuid4().hex)
        now = _now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            resolved_store_id: int | None = int(store_id) if store_id else None
            resolved_app_id = str(store_app_id or "").strip()
            if resolved_store_id is not None:
                row = conn.execute(
                    "SELECT app_id FROM stores WHERE id=?",
                    (resolved_store_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("目标店铺不存在")
                if resolved_app_id and row["app_id"] != resolved_app_id:
                    raise ValueError("目标店铺与 AppID 不一致")
                resolved_app_id = str(row["app_id"])
            conn.execute(
                """
                INSERT INTO sales_tasks(
                    task_id, source_url, canonical_url, store_id, store_app_id,
                    device_id, buyer_ref, sku_selection_mode, specified_sku_id,
                    specified_specs_json, purchase_quantity, price_cap_cents,
                    status, simulation_mode, created_by, updated_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_task_id,
                    source_url,
                    canonical_url,
                    resolved_store_id,
                    resolved_app_id,
                    device_id,
                    buyer_ref,
                    str(sku_selection_mode),
                    specified_sku_id,
                    _json(dict(specified_specs or {})),
                    int(purchase_quantity),
                    max(0, int(price_cap_cents)),
                    SalesTaskStatus.PENDING.value,
                    1 if simulation_mode else 0,
                    created_by,
                    created_by,
                    now,
                    now,
                ),
            )
            self.append_audit(
                conn,
                "task_created",
                actor=created_by,
                task_id=normalized_task_id,
                decision="created",
                detail={
                    "canonical_url": canonical_url,
                    "store_app_id": resolved_app_id,
                    "device_id": device_id,
                    "quantity": int(purchase_quantity),
                    "simulation_mode": bool(simulation_mode),
                },
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise RuntimeError("任务创建后无法读取")
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sales_tasks WHERE task_id=?",
                (str(task_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_tasks(
        self,
        *,
        status: str = "",
        device_id: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sales_tasks WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if device_id:
            sql += " AND device_id=?"
            args.append(device_id)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def list_canonical_urls(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT canonical_url FROM sales_tasks"
            ).fetchall()
        return {str(row["canonical_url"]) for row in rows}

    def set_task_reason(
        self,
        task_id: str,
        reason: str,
        *,
        actor: str = "",
    ) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE sales_tasks
                   SET status_reason=?, updated_by=?, updated_at=?
                 WHERE task_id=?
                """,
                (str(reason), actor, _now(), str(task_id)),
            )
            if cursor.rowcount != 1:
                raise KeyError("任务不存在")

    def transition_task(
        self,
        task_id: str,
        event: SalesEvent | str,
        *,
        reason: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM sales_tasks WHERE task_id=?",
                (str(task_id),),
            ).fetchone()
            if row is None:
                raise KeyError("任务不存在")
            current = SalesTaskStatus(row["status"])
            target = transition_task(current, event)
            now = _now()
            cursor = conn.execute(
                """
                UPDATE sales_tasks
                   SET status=?, status_reason=?, updated_by=?,
                       updated_at=?
                 WHERE task_id=? AND status=?
                """,
                (
                    target.value,
                    reason,
                    actor,
                    now,
                    str(task_id),
                    current.value,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("任务状态已被其他操作更新，请重新读取")
            self.append_audit(
                conn,
                "task_transition",
                actor=actor,
                task_id=str(task_id),
                decision=target.value,
                detail={
                    "from": current.value,
                    "event": SalesEvent(event).value,
                    "reason": reason,
                },
            )
        result = self.get_task(task_id)
        if result is None:
            raise RuntimeError("任务状态更新后无法读取")
        return result

    def set_submission_id(
        self,
        task_id: str,
        submission_id: str = "",
        *,
        actor: str = "",
    ) -> str:
        value = str(submission_id or uuid.uuid4().hex)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, submission_id
                  FROM sales_tasks
                 WHERE task_id=?
                """,
                (str(task_id),),
            ).fetchone()
            if row is None:
                raise KeyError("任务不存在")
            if row["status"] != SalesTaskStatus.AWAITING_USER_CONFIRMATION:
                raise ValueError("当前任务状态不能创建提交编号")
            if row["submission_id"]:
                if row["submission_id"] == value:
                    return value
                raise ValueError("任务已经存在其他提交编号")
            conn.execute(
                """
                UPDATE sales_tasks
                   SET submission_id=?, updated_by=?, updated_at=?
                 WHERE task_id=?
                """,
                (value, actor, _now(), str(task_id)),
            )
            self.append_audit(
                conn,
                "submission_id_created",
                actor=actor,
                task_id=str(task_id),
                decision=value,
            )
        return value

    def add_sku_snapshot(
        self,
        task_id: str,
        candidate: SkuCandidate,
        *,
        product_id: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sales_task_sku_snapshots(
                    task_id, product_id, sku_id, specs_json, price_cents,
                    stock, limit_min, limit_max, available, complete,
                    is_default, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(task_id),
                    product_id,
                    candidate.sku_id,
                    _json(dict(candidate.specs)),
                    int(candidate.price_cents),
                    int(candidate.stock),
                    max(1, int(candidate.limit_min)),
                    candidate.limit_max,
                    1 if candidate.available else 0,
                    1 if candidate.complete else 0,
                    1 if candidate.default else 0,
                    _now(),
                ),
            )

    def record_order_verification(
        self,
        task_id: str,
        submission_id: str,
        *,
        source: str,
        outcome: str,
        trusted: bool,
        order_id: str = "",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        trusted_source = str(source or "").strip()
        is_trusted = bool(
            trusted
            and trusted_source in TRUSTED_ORDER_VERIFICATION_SOURCES
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sales_order_verifications(
                    task_id, submission_id, source, outcome, trusted,
                    order_id, detail_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    submission_id,
                    source,
                    outcome,
                    1 if is_trusted else 0,
                    order_id,
                    _json(dict(detail or {})),
                    _now(),
                ),
            )
            self.append_audit(
                conn,
                "order_verification",
                task_id=task_id,
                decision=outcome,
                detail={
                    "submission_id": submission_id,
                    "source": source,
                    "trusted": is_trusted,
                    "order_id": order_id,
                },
            )

    def create_verified_order(
        self,
        *,
        task_id: str,
        submission_id: str,
        order_id: str,
        sku_id: str,
        quantity: int,
        store_id: int = 0,
        store_app_id: str = "",
        product_amount_cents: int = 0,
        freight_cents: int = 0,
        discount_cents: int = 0,
        payable_cents: int = 0,
        order_status: str = "",
        payment_status: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        if not order_id:
            raise ValueError("可信订单必须包含订单号")
        now = _now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                """
                SELECT status, submission_id
                  FROM sales_tasks
                 WHERE task_id=?
                """,
                (task_id,),
            ).fetchone()
            if task is None:
                raise KeyError("任务不存在")
            if task["submission_id"] != submission_id:
                raise ValueError("提交编号与任务不一致")
            if task["status"] not in {
                SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION.value,
                SalesTaskStatus.PENDING_PAYMENT.value,
            }:
                raise ValueError("任务状态不允许写入订单")
            verification = conn.execute(
                """
                SELECT 1 FROM sales_order_verifications
                 WHERE task_id=? AND submission_id=? AND order_id=?
                   AND trusted=1
                   AND outcome IN ('created', 'pending_payment', 'paid')
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (task_id, submission_id, order_id),
            ).fetchone()
            if verification is None:
                raise ValueError("缺少可信订单核验结果，禁止写入订单")
            existing = conn.execute(
                "SELECT * FROM sales_orders WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                if existing["order_id"] != order_id:
                    raise ValueError("一个任务已关联其他订单，禁止拆成多笔")
                return dict(existing)
            conn.execute(
                """
                INSERT INTO sales_orders(
                    task_id, submission_id, store_id, store_app_id,
                    order_id, sku_id, quantity, product_amount_cents,
                    freight_cents, discount_cents, payable_cents,
                    order_status, payment_status, verified_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    submission_id,
                    int(store_id) if store_id else None,
                    store_app_id,
                    order_id,
                    sku_id,
                    int(quantity),
                    max(0, int(product_amount_cents)),
                    max(0, int(freight_cents)),
                    max(0, int(discount_cents)),
                    max(0, int(payable_cents)),
                    order_status,
                    payment_status,
                    now,
                    now,
                    now,
                ),
            )
            self.append_audit(
                conn,
                "verified_order_created",
                actor=actor,
                task_id=task_id,
                decision=order_id,
                detail={
                    "submission_id": submission_id,
                    "payable_cents": int(payable_cents),
                },
            )
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sales_orders WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return dict(row)

    def list_orders(self, *, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sales_orders
                 ORDER BY created_at DESC, id DESC
                 LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_device(
        self,
        device_id: str,
        token: str,
        *,
        display_name: str = "",
        capabilities: Mapping[str, Any] | None = None,
        app_version: str = "",
    ) -> None:
        if not str(device_id).strip() or not str(token).strip():
            raise ValueError("设备编号和认证凭证不能为空")
        token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sales_devices(
                    device_id, display_name, token_hash, status,
                    capabilities_json, app_version, last_seen_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'online', ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    token_hash=excluded.token_hash,
                    status='online',
                    capabilities_json=excluded.capabilities_json,
                    app_version=excluded.app_version,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at
                """,
                (
                    device_id,
                    display_name,
                    token_hash,
                    _json(dict(capabilities or {})),
                    app_version,
                    now,
                    now,
                    now,
                ),
            )

    def list_devices(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT device_id, display_name, status, capabilities_json,
                       app_version, last_seen_at, created_at, updated_at
                  FROM sales_devices
                 ORDER BY last_seen_at DESC, device_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def authenticate_device(
        self,
        device_id: str,
        token: str,
        *,
        touch: bool = True,
    ) -> bool:
        token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT token_hash, status
                  FROM sales_devices
                 WHERE device_id=?
                """,
                (str(device_id),),
            ).fetchone()
            if (
                row is None
                or row["status"] not in {"online", "busy"}
                or row["token_hash"] != token_hash
            ):
                return False
            if touch:
                now = _now()
                conn.execute(
                    """
                    UPDATE sales_devices
                       SET last_seen_at=?, updated_at=?
                     WHERE device_id=?
                    """,
                    (now, now, str(device_id)),
                )
            return True

    def claim_task_lease(
        self,
        task_id: str,
        device_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        now_text = now.isoformat(timespec="seconds")
        expires = (now + timedelta(seconds=max(30, int(lease_seconds)))).isoformat(
            timespec="seconds"
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                "SELECT status, device_id FROM sales_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise KeyError("任务不存在")
            if task["status"] in {"completed", "closed", "failed", "paid"}:
                raise ValueError("任务当前状态不可领取")
            existing = conn.execute(
                "SELECT * FROM sales_device_leases WHERE task_id=?",
                (task_id,),
            ).fetchone()
            existing_active = bool(
                existing is not None
                and existing["status"] == "active"
                and existing["lease_expires_at"] > now_text
            )
            if (
                task["device_id"]
                and task["device_id"] != device_id
                and existing_active
            ):
                raise ValueError("任务已分配给其他设备")
            if existing is not None:
                if existing["status"] == "active" and existing[
                    "lease_expires_at"
                ] > now_text:
                    raise ValueError("任务租约仍被其他设备持有")
                attempt = int(existing["attempt_no"]) + 1
                conn.execute(
                    """
                    UPDATE sales_device_leases
                       SET device_id=?, lease_token_hash=?, lease_expires_at=?,
                           heartbeat_at=?, attempt_no=?, status='active'
                     WHERE task_id=?
                    """,
                    (
                        device_id,
                        lease_hash,
                        expires,
                        now_text,
                        attempt,
                        task_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sales_device_leases(
                        task_id, device_id, lease_token_hash,
                        lease_expires_at, heartbeat_at, attempt_no, status
                    ) VALUES (?, ?, ?, ?, ?, 1, 'active')
                    """,
                    (
                        task_id,
                        device_id,
                        lease_hash,
                        expires,
                        now_text,
                    ),
                )
            conn.execute(
                """
                UPDATE sales_tasks
                   SET device_id=?, updated_at=?
                 WHERE task_id=?
                """,
                (device_id, now_text, task_id),
            )
            self.append_audit(
                conn,
                "task_lease_claimed",
                task_id=task_id,
                device_id=device_id,
                decision="active",
                detail={"lease_expires_at": expires},
            )
            row = conn.execute(
                "SELECT * FROM sales_device_leases WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return dict(row)

    def heartbeat_lease(
        self,
        task_id: str,
        device_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        now_text = now.isoformat(timespec="seconds")
        expires = (now + timedelta(seconds=max(30, int(lease_seconds)))).isoformat(
            timespec="seconds"
        )
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE sales_device_leases
                   SET lease_expires_at=?, heartbeat_at=?
                 WHERE task_id=? AND device_id=? AND lease_token_hash=?
                   AND status='active'
                """,
                (
                    expires,
                    now_text,
                    str(task_id),
                    str(device_id),
                    lease_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("任务租约不存在、已过期或凭证不匹配")
            conn.execute(
                """
                UPDATE sales_devices
                   SET last_seen_at=?, updated_at=?
                 WHERE device_id=?
                """,
                (now_text, now_text, str(device_id)),
            )
            row = conn.execute(
                "SELECT * FROM sales_device_leases WHERE task_id=?",
                (str(task_id),),
            ).fetchone()
        return dict(row)

    def release_lease(
        self,
        task_id: str,
        device_id: str,
        lease_token: str,
    ) -> None:
        lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE sales_device_leases
                   SET status='released'
                 WHERE task_id=? AND device_id=? AND lease_token_hash=?
                   AND status='active'
                """,
                (str(task_id), str(device_id), lease_hash),
            )
            if cursor.rowcount != 1:
                raise ValueError("任务租约不存在或凭证不匹配")
            self.append_audit(
                conn,
                "task_lease_released",
                task_id=str(task_id),
                device_id=str(device_id),
            )

    def upsert_whitelist(self, **values: Any) -> int:
        required = {
            "store_id",
            "store_app_id",
            "buyer_identifier_type",
            "buyer_identifier",
            "allowed_after_sale_types",
            "single_refund_limit_cents",
            "daily_refund_limit_cents",
            "monthly_refund_limit_cents",
            "valid_from",
            "created_by",
        }
        missing = sorted(required - set(values))
        if missing:
            raise ValueError("白名单缺少字段：" + "、".join(missing))
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sales_buyer_whitelist(
                    store_id, store_app_id, buyer_identifier_type,
                    buyer_identifier, display_name, enabled,
                    allowed_after_sale_types_json,
                    single_refund_limit_cents, daily_refund_limit_cents,
                    monthly_refund_limit_cents, allowed_order_ids_json,
                    valid_from, valid_to, emergency_stop, created_by,
                    updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    store_app_id, buyer_identifier_type, buyer_identifier
                ) DO UPDATE SET
                    store_id=excluded.store_id,
                    display_name=excluded.display_name,
                    enabled=excluded.enabled,
                    allowed_after_sale_types_json=
                        excluded.allowed_after_sale_types_json,
                    single_refund_limit_cents=
                        excluded.single_refund_limit_cents,
                    daily_refund_limit_cents=
                        excluded.daily_refund_limit_cents,
                    monthly_refund_limit_cents=
                        excluded.monthly_refund_limit_cents,
                    allowed_order_ids_json=excluded.allowed_order_ids_json,
                    valid_from=excluded.valid_from,
                    valid_to=excluded.valid_to,
                    emergency_stop=excluded.emergency_stop,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (
                    int(values["store_id"]),
                    str(values["store_app_id"]),
                    str(values["buyer_identifier_type"]).casefold(),
                    str(values["buyer_identifier"]),
                    str(values.get("display_name") or ""),
                    1 if values.get("enabled", True) else 0,
                    _json(list(values["allowed_after_sale_types"])),
                    int(values["single_refund_limit_cents"]),
                    int(values["daily_refund_limit_cents"]),
                    int(values["monthly_refund_limit_cents"]),
                    _json(list(values.get("allowed_order_ids") or [])),
                    str(values["valid_from"]),
                    str(values["valid_to"]) if values.get("valid_to") else None,
                    1 if values.get("emergency_stop") else 0,
                    str(values["created_by"]),
                    str(values.get("updated_by") or values["created_by"]),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM sales_buyer_whitelist
                 WHERE store_app_id=? AND buyer_identifier_type=?
                   AND buyer_identifier=?
                """,
                (
                    str(values["store_app_id"]),
                    str(values["buyer_identifier_type"]).casefold(),
                    str(values["buyer_identifier"]),
                ),
            ).fetchone()
            assert row is not None
            self.append_audit(
                conn,
                "whitelist_upserted",
                actor=str(values.get("updated_by") or values["created_by"]),
                decision="enabled" if values.get("enabled", True) else "disabled",
                detail={
                    "store_app_id": str(values["store_app_id"]),
                    "buyer_identifier_type": str(
                        values["buyer_identifier_type"]
                    ),
                },
            )
            return int(row["id"])

    def list_whitelist(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sales_buyer_whitelist
                 ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_audit_logs(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sales_audit_logs
                 ORDER BY created_at DESC, id DESC
                 LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]
