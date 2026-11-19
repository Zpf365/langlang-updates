from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Callable

from ..domain.enums import (
    TaskItemStatus,
    TaskStatus,
)
from ..domain.models import Task, TaskExecutionItem
from ..domain.rules import StoreIsolationError
from ..infrastructure.repository import (
    DongxiaoRepository,
    EntityNotFound,
)

PRODUCT_SKU_VALIDATION = "product_sku_validation"
ORDER_VERIFICATION = "order_verification"
AFTERSALE_VERIFICATION = "aftersale_verification"
AFTERSALE_DECISION = "aftersale_decision"
DESKTOP_CART_ADD = "desktop_cart_add"


class PlatformWriteDisabled(RuntimeError):
    pass


class DongxiaoTaskService:
    def __init__(
        self,
        repository: DongxiaoRepository,
        *,
        platform_write_gate: Callable[[str], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.platform_write_gate = platform_write_gate

    def _assert_platform_write_allowed(self, store) -> None:
        if self.platform_write_gate is None:
            return
        if not bool(self.platform_write_gate(str(store.app_id))):
            raise PlatformWriteDisabled(
                "当前会话或店铺未通过真实只读验收，"
                "禁止创建或执行新的平台写任务"
            )

    def create_pool_validation_task(
        self,
        store,
        pool_id: str,
        *,
        idempotency_key: str,
        actor_id: str,
        quantity: int = 1,
        max_attempts: int = 3,
        priority: int = 0,
    ) -> tuple[Task, bool]:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise TypeError("任务商品数量必须是整数")
        if quantity <= 0:
            raise ValueError("任务商品数量必须大于 0")
        pool = self.repository.get_pool(
            pool_id,
            store_id=int(store.id),
        )
        if pool is None or pool.store_app_id != str(store.app_id):
            raise StoreIsolationError("商品池不属于当前店铺")
        rows = self.repository.list_pool_items(
            pool_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )
        if not rows:
            raise ValueError("空商品池不能创建任务")
        signatures = [
            (
                "product",
                str(row["product_id"]),
                str(row["product_id"]),
                str(row["sku_id"] or ""),
            )
            for row in rows
        ]
        digest = hashlib.sha256(
            json.dumps(
                signatures,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        now = datetime.now(UTC)
        task = Task(
            task_id=uuid.uuid4().hex,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            task_type=PRODUCT_SKU_VALIDATION,
            idempotency_key=str(idempotency_key).strip(),
            status=TaskStatus.PENDING,
            total_items=len(rows),
            succeeded_items=0,
            failed_items=0,
            created_at=now,
            updated_at=now,
            metadata={
                "pool_id": str(pool_id),
                "pool_name": pool.name,
                "quantity": quantity,
                "item_digest": digest,
                "actor_id": str(actor_id),
            },
            priority=int(priority),
            max_attempts=int(max_attempts),
            execution_mode="local_safe",
        )
        items = [
            TaskExecutionItem(
                item_id=uuid.uuid4().hex,
                task_id=task.task_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                subject_type="product",
                subject_id=str(row["product_id"]),
                product_id=str(row["product_id"]),
                sku_id=str(row["sku_id"] or ""),
                status=TaskItemStatus.PENDING,
                attempt_count=0,
                max_attempts=task.max_attempts,
                created_at=now,
                updated_at=now,
                payload={"quantity": quantity},
            )
            for row in rows
        ]
        saved, created = self.repository.create_task_with_execution_items(
            task,
            items,
        )
        return saved, created

    def list_items(self, task_id: str, *, store) -> list[TaskExecutionItem]:
        task = self.repository.get_task(
            task_id,
            store_app_id=str(store.app_id),
        )
        if task is None:
            raise EntityNotFound("任务不存在或不属于当前店铺")
        if task.store_id != int(store.id):
            raise StoreIsolationError("任务不属于当前店铺")
        return self.repository.list_task_execution_items(
            task_id,
            store_app_id=str(store.app_id),
        )

    def list_tasks(self, store) -> list[Task]:
        return self.repository.list_tasks(int(store.id))

    def list_history(self, store_app_id: str) -> list[Task]:
        return self.repository.list_tasks_by_store_app_id(
            str(store_app_id)
        )

    def statistics(self, store):
        return self.repository.task_statistics(int(store.id))

    def list_attempts(self, store, task_id: str):
        task = self.repository.get_task(
            task_id,
            store_app_id=str(store.app_id),
        )
        if task is None or task.store_id != int(store.id):
            raise EntityNotFound("任务不存在或不属于当前店铺")
        return self.repository.list_task_attempts(
            task_id,
            store_app_id=str(store.app_id),
        )

    def create_order_verification_task(
        self,
        store,
        order_ids: list[str],
        *,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime | None = None,
        max_attempts: int = 3,
    ) -> tuple[Task, bool]:
        normalized = tuple(
            dict.fromkeys(
                str(order_id).strip()
                for order_id in order_ids
                if str(order_id).strip()
            )
        )
        if not normalized:
            raise ValueError("订单核验任务至少需要一个订单号")
        return self._create_subject_task(
            store,
            task_type=ORDER_VERIFICATION,
            subject_type="order",
            subjects=[(order_id, "", "") for order_id in normalized],
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            requested_at=requested_at,
            max_attempts=max_attempts,
            execution_mode="official_read",
            payload={},
        )

    def create_aftersale_verification_task(
        self,
        store,
        case_ids: list[str],
        *,
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime | None = None,
        max_attempts: int = 3,
    ) -> tuple[Task, bool]:
        normalized = tuple(
            dict.fromkeys(
                str(case_id).strip()
                for case_id in case_ids
                if str(case_id).strip()
            )
        )
        if not normalized:
            raise ValueError("售后核验任务至少需要一个售后单号")
        normalized = tuple(
            (
                case.platform_case_id
                if (
                    case := self.repository.get_aftersale_case(
                        int(store.id),
                        case_id,
                    )
                )
                is not None
                else case_id
            )
            for case_id in normalized
        )
        return self._create_subject_task(
            store,
            task_type=AFTERSALE_VERIFICATION,
            subject_type="aftersale",
            subjects=[(case_id, "", "") for case_id in normalized],
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            requested_at=requested_at,
            max_attempts=max_attempts,
            execution_mode="official_read",
            payload={},
        )

    def create_aftersale_decision_task(
        self,
        store,
        case_id: str,
        *,
        action: str,
        approved_amount_cents: int,
        idempotency_key: str,
        actor_id: str,
        address_id: str = "",
        accept_type: int | None = None,
        reject_reason_type: int = 0,
        reject_reason: str = "",
        requested_at: datetime | None = None,
        max_attempts: int = 1,
    ) -> tuple[Task, bool]:
        self._assert_platform_write_allowed(store)
        normalized_action = str(action or "").strip()
        if normalized_action not in {"accept", "reject"}:
            raise ValueError("售后执行动作必须是 accept 或 reject")
        case = self.repository.get_aftersale_case(
            int(store.id),
            str(case_id),
        )
        if case is None:
            raise ValueError("售后工单不存在")
        payload: dict[str, object] = {
            "action": normalized_action,
            "approved_amount_cents": int(approved_amount_cents),
            "initial_platform_status": case.platform_status,
            "address_id": str(address_id),
            "reject_reason_type": int(reject_reason_type),
            "reject_reason": str(reject_reason),
        }
        if accept_type is not None:
            payload["accept_type"] = int(accept_type)
        existing_task = self.repository.find_task_for_execution_subject(
            store_app_id=str(store.app_id),
            subject_type="aftersale",
            subject_id=case.platform_case_id,
            task_type=AFTERSALE_DECISION,
        )
        if existing_task is not None:
            if existing_task.idempotency_key == str(idempotency_key):
                return existing_task, False
            raise ValueError("当前售后已有执行中或已完成的退款任务")
        return self._create_subject_task(
            store,
            task_type=AFTERSALE_DECISION,
            subject_type="aftersale",
            subjects=[(case.platform_case_id, "", "")],
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            requested_at=requested_at,
            max_attempts=max_attempts,
            execution_mode="platform_write",
            payload=payload,
        )

    def create_desktop_cart_task(
        self,
        store,
        *,
        batch_id: str,
        account_id: str,
        items: list[dict[str, object]],
        idempotency_key: str,
        actor_id: str,
        execution_mode: str,
        speed_mode: str,
        interval_ms: int,
        concurrency: int,
        adapter_mode: str,
        requested_at: datetime | None = None,
        max_attempts: int = 1,
    ) -> tuple[Task, bool]:
        if execution_mode == "platform_write":
            self._assert_platform_write_allowed(store)
        if not items:
            raise ValueError("批量加购任务至少需要一个商品")
        now = requested_at or datetime.now(UTC)
        normalized_items: list[dict[str, object]] = []
        for item in items:
            product_id = str(item.get("product_id") or "").strip()
            sku_id = str(item.get("sku_id") or "").strip()
            if not product_id:
                raise ValueError("批量加购任务商品 ID 不能为空")
            normalized_items.append(
                {
                    **dict(item),
                    "batch_id": str(batch_id),
                    "account_id": str(account_id),
                    "desktop_cart": True,
                    "adapter_mode": str(adapter_mode),
                    "speed_mode": str(speed_mode),
                    "interval_ms": int(interval_ms),
                    "concurrency": int(concurrency),
                    "product_id": product_id,
                    "sku_id": sku_id,
                }
            )
        signatures = [
            (
                "cart",
                f"{item['product_id']}:{item.get('sku_id') or ''}",
                str(item["product_id"]),
                str(item.get("sku_id") or ""),
            )
            for item in normalized_items
        ]
        digest = hashlib.sha256(
            json.dumps(
                signatures,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        metadata = {
            "subject_type": "cart",
            "batch_id": str(batch_id),
            "account_id": str(account_id),
            "adapter_mode": str(adapter_mode),
            "speed_mode": str(speed_mode),
            "interval_ms": int(interval_ms),
            "concurrency": int(concurrency),
            "item_digest": digest,
            "actor_id": str(actor_id),
        }
        task = Task(
            task_id=uuid.uuid4().hex,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            task_type=DESKTOP_CART_ADD,
            idempotency_key=str(idempotency_key).strip(),
            status=TaskStatus.PENDING,
            total_items=len(normalized_items),
            succeeded_items=0,
            failed_items=0,
            created_at=now,
            updated_at=now,
            metadata=metadata,
            max_attempts=int(max_attempts),
            execution_mode=execution_mode,
        )
        execution_items = [
            TaskExecutionItem(
                item_id=uuid.uuid4().hex,
                task_id=task.task_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                subject_type="product",
                subject_id=(
                    f"{item['product_id']}:{item.get('sku_id') or ''}"
                ),
                product_id=str(item["product_id"]),
                sku_id=str(item.get("sku_id") or ""),
                status=TaskItemStatus.PENDING,
                attempt_count=0,
                max_attempts=task.max_attempts,
                created_at=now,
                updated_at=now,
                payload=dict(item),
            )
            for item in normalized_items
        ]
        return self.repository.create_task_with_execution_items(
            task,
            execution_items,
        )

    def pause(self, store, task_id: str, *, actor_id: str = "") -> Task:
        return self.repository.transition_task(
            task_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            event="pause",
            actor_id=actor_id,
            reason="用户暂停任务",
        )

    def resume(self, store, task_id: str, *, actor_id: str = "") -> Task:
        return self.repository.transition_task(
            task_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            event="resume",
            actor_id=actor_id,
            reason="用户恢复任务",
        )

    def cancel(self, store, task_id: str, *, actor_id: str = "") -> Task:
        task = self.repository.transition_task(
            task_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            event="cancel",
            actor_id=actor_id,
            reason="用户取消任务",
        )
        self.repository.cancel_pending_execution_items(
            task_id,
            store_app_id=str(store.app_id),
        )
        items = self.repository.list_task_execution_items(
            task_id,
            store_app_id=str(store.app_id),
            limit=100000,
        )
        self.repository.update_task_progress(
            task_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            succeeded_items=sum(
                item.status.value == "succeeded" for item in items
            ),
            failed_items=sum(
                item.status.value == "failed" for item in items
            ),
            status_reason="用户取消任务",
            finished_at=datetime.now(UTC),
        )
        saved = self.repository.get_task(
            task_id,
            store_app_id=str(store.app_id),
        )
        return saved or task

    def retry_failed(
        self,
        store,
        task_id: str,
        *,
        actor_id: str = "",
    ) -> Task:
        task = self.repository.get_task(
            task_id,
            store_app_id=str(store.app_id),
        )
        if task is None or task.store_id != int(store.id):
            raise EntityNotFound("任务不存在或不属于当前店铺")
        reset = self.repository.retry_failed_execution_items(
            task_id,
            store_app_id=str(store.app_id),
        )
        if reset == 0:
            raise ValueError("没有可重试的失败明细")
        return self.repository.transition_task(
            task_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            event="resume",
            actor_id=actor_id,
            reason="用户重试失败明细",
        )

    def _create_subject_task(
        self,
        store,
        *,
        task_type: str,
        subject_type: str,
        subjects: list[tuple[str, str, str]],
        idempotency_key: str,
        actor_id: str,
        requested_at: datetime | None,
        max_attempts: int,
        execution_mode: str,
        payload: dict[str, object],
    ) -> tuple[Task, bool]:
        if execution_mode == "platform_write":
            self._assert_platform_write_allowed(store)
        now = requested_at or datetime.now(UTC)
        signatures = [
            (subject_type, subject_id, product_id, sku_id)
            for subject_id, product_id, sku_id in subjects
        ]
        digest = hashlib.sha256(
            json.dumps(
                signatures,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        task = Task(
            task_id=uuid.uuid4().hex,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            task_type=task_type,
            idempotency_key=str(idempotency_key).strip(),
            status=TaskStatus.PENDING,
            total_items=len(subjects),
            succeeded_items=0,
            failed_items=0,
            created_at=now,
            updated_at=now,
            metadata={
                "subject_type": subject_type,
                "item_digest": digest,
                "actor_id": str(actor_id),
                "payload": payload,
            },
            max_attempts=int(max_attempts),
            execution_mode=execution_mode,
        )
        items = [
            TaskExecutionItem(
                item_id=uuid.uuid4().hex,
                task_id=task.task_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                subject_type=subject_type,
                subject_id=subject_id,
                product_id=product_id,
                sku_id=sku_id,
                status=TaskItemStatus.PENDING,
                attempt_count=0,
                max_attempts=task.max_attempts,
                created_at=now,
                updated_at=now,
                payload=payload,
            )
            for subject_id, product_id, sku_id in subjects
        ]
        saved, created = self.repository.create_task_with_execution_items(
            task,
            items,
        )
        return saved, created
