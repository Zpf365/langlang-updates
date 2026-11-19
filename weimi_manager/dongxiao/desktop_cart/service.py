from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..application.task_service import DongxiaoTaskService
from ..domain.enums import AuditResult, TaskItemStatus, TaskStatus
from ..domain.models import AuditEvent
from ..domain.rules import require_permission
from ..infrastructure.repository import DongxiaoRepository
from .adapters import build_cart_adapter
from .domain import (
    CartAccount,
    CartAccountStatus,
    CartAdapterCapability,
    CartAdapterMode,
    CartBatch,
    CartBatchStatus,
    CartResultStatus,
    CartSkuMode,
    CartSpeedMode,
    OutOfStockPolicy,
    PendingOrderDraft,
    PendingOrderStatus,
    speed_profile_for,
)
from .repository import DesktopCartRepository
from .rules import (
    CartPreviewItem,
    aggregate_cart_results,
    batch_number_for,
    resolve_cart_item,
)


class DesktopCartServiceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DesktopCartPreview:
    pool_id: str
    store_id: int
    store_app_id: str
    total_products: int
    ready_count: int
    skipped_count: int
    failed_count: int
    total_amount_cents: int
    items: tuple[CartPreviewItem, ...]
    account_count: int
    adapter_mode: CartAdapterMode
    capability: CartAdapterCapability
    warnings: tuple[str, ...] = field(default_factory=tuple)


class DesktopCartService:
    def __init__(
        self,
        repository: DesktopCartRepository,
        catalog_repository: DongxiaoRepository,
        task_service: DongxiaoTaskService,
        *,
        scheduler=None,
        sleeper=time.sleep,
    ) -> None:
        self.repository = repository
        self.catalog_repository = catalog_repository
        self.task_service = task_service
        self.scheduler = scheduler
        self.sleeper = sleeper

    def capability(
        self,
        mode: CartAdapterMode | str,
    ) -> CartAdapterCapability:
        return build_cart_adapter(
            mode,
            self.repository,
        ).capability()

    def create_account(
        self,
        store,
        *,
        display_name: str,
        adapter_mode: CartAdapterMode | str,
        permission_checker,
        actor_id: str,
    ) -> CartAccount:
        require_permission(
            permission_checker,
            "dongxiao_cart_account_manage",
        )
        now = datetime.now(UTC)
        mode = CartAdapterMode(adapter_mode)
        account = CartAccount(
            account_id=uuid.uuid4().hex,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            display_name=str(display_name).strip(),
            adapter_mode=mode,
            status=(
                CartAccountStatus.MOCK_READY
                if mode is CartAdapterMode.MOCK
                else CartAccountStatus.PENDING_AUTHORIZATION
            ),
            authorization_ref="",
            created_at=now,
            updated_at=now,
            last_checked_at=now,
        )
        saved, _created = self.repository.create_account(account)
        self._audit(
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            actor_id=str(actor_id),
            action="cart_account_created",
            entity_type="cart_account",
            entity_id=saved.account_id,
            result="success",
            detail={
                "display_name": saved.display_name,
                "adapter_mode": saved.adapter_mode.value,
            },
        )
        return saved

    def list_accounts(self, store, *, permission_checker) -> list[CartAccount]:
        require_permission(
            permission_checker,
            "dongxiao_cart_account_manage",
        )
        return [
            account
            for account in self.repository.list_accounts(store_id=int(store.id))
            if account.store_app_id == str(store.app_id)
        ]

    def check_account(
        self,
        account: CartAccount,
        *,
        permission_checker,
        actor_id: str,
    ) -> CartAccount:
        require_permission(
            permission_checker,
            "dongxiao_cart_account_manage",
        )
        adapter = build_cart_adapter(account.adapter_mode, self.repository)
        try:
            status = adapter.check_account(account)
            error = ""
        except Exception as exc:
            status = CartAccountStatus.DISCONNECTED
            error = str(exc)
        saved = self.repository.update_account_status(
            account.account_id,
            status,
            error=error,
        )
        self._audit(
            store_id=saved.store_id,
            store_app_id=saved.store_app_id,
            actor_id=str(actor_id),
            action="cart_account_checked",
            entity_type="cart_account",
            entity_id=saved.account_id,
            result="failed" if error else "success",
            detail={"status": saved.status.value, "error": error},
        )
        return saved

    def preview_batch(
        self,
        store,
        *,
        pool_id: str,
        quantity_per_product: int,
        sku_mode: CartSkuMode | str,
        specified_sku_id: str,
        out_of_stock_policy: OutOfStockPolicy | str,
        products_per_batch: int,
        account_ids: list[str],
        adapter_mode: CartAdapterMode | str,
        permission_checker,
    ) -> DesktopCartPreview:
        require_permission(
            permission_checker,
            "dongxiao_cart_task_preview",
        )
        pool = self.catalog_repository.get_pool(
            pool_id,
            store_id=int(store.id),
        )
        if pool is None or pool.store_app_id != str(store.app_id):
            raise ValueError("商品池不存在或不属于当前店铺")
        rows = self.catalog_repository.list_pool_items(
            pool_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )
        if not rows:
            raise ValueError("商品池为空，不能预览批量加购")
        if products_per_batch <= 0:
            raise ValueError("每批商品数量必须大于 0")
        accounts = tuple(
            self.repository.get_account(
                account_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
            )
            for account_id in dict.fromkeys(str(item) for item in account_ids)
        )
        if not accounts or any(account is None for account in accounts):
            raise ValueError("批量加购至少需要一个当前店铺的执行账号")
        mode = CartAdapterMode(adapter_mode)
        if any(account.adapter_mode is not mode for account in accounts if account):
            raise ValueError("批次适配器与执行账号授权方式不一致")
        snapshots = self.catalog_repository.catalog_snapshot_for_products(
            int(store.id),
            [str(row["product_id"]) for row in rows],
        )
        preview_items: list[CartPreviewItem] = []
        warnings: list[str] = []
        for row in rows:
            product_id = str(row["product_id"])
            snapshot = snapshots.get(product_id)
            if snapshot is None:
                product = None
                candidates = ()
            else:
                product, candidates = snapshot
            effective_sku = (
                str(specified_sku_id or "").strip()
                or str(row["sku_id"] or "").strip()
            )
            request, status, reason = resolve_cart_item(
                product=product,
                candidates=candidates,
                quantity=int(quantity_per_product),
                sku_mode=sku_mode,
                specified_sku_id=effective_sku,
                out_of_stock_policy=out_of_stock_policy,
            )
            if request is not None:
                preview_items.append(
                    CartPreviewItem(
                        product_id=request.product_id,
                        title=request.title,
                        sku_id=request.sku_id,
                        specs=request.specs,
                        unit_price_cents=request.unit_price_cents,
                        quantity=request.quantity,
                        status="ready",
                    )
                )
            else:
                preview_items.append(
                    CartPreviewItem(
                        product_id=product_id,
                        title=product.title if product else "商品数据缺失",
                        sku_id=effective_sku,
                        specs=(),
                        unit_price_cents=product.min_price_cents if product else 0,
                        quantity=int(quantity_per_product),
                        status=status,
                        reason=reason,
                    )
                )
        ready_count = sum(item.status == "ready" for item in preview_items)
        skipped_count = sum(item.status == "skipped" for item in preview_items)
        failed_count = sum(item.status == "failed" for item in preview_items)
        if skipped_count:
            warnings.append(f"{skipped_count} 个商品将按缺货规则跳过")
        if failed_count:
            warnings.append(f"{failed_count} 个商品未通过预检，不会进入执行任务")
        return DesktopCartPreview(
            pool_id=pool_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            total_products=len(preview_items),
            ready_count=ready_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            total_amount_cents=sum(
                item.unit_price_cents * item.quantity
                for item in preview_items
                if item.status == "ready"
            ),
            items=tuple(preview_items),
            account_count=len(accounts),
            adapter_mode=mode,
            capability=self.capability(mode),
            warnings=tuple(warnings),
        )

    def create_batch(
        self,
        store,
        *,
        name: str,
        idempotency_key: str,
        preview: DesktopCartPreview,
        quantity_per_product: int,
        products_per_batch: int,
        sku_mode: CartSkuMode | str,
        specified_sku_id: str,
        out_of_stock_policy: OutOfStockPolicy | str,
        speed_mode: CartSpeedMode | str,
        interval_ms: int | None,
        concurrency: int | None,
        account_ids: list[str],
        permission_checker,
        actor_id: str,
    ) -> tuple[CartBatch, bool]:
        require_permission(
            permission_checker,
            "dongxiao_cart_task_create",
        )
        if preview.ready_count <= 0:
            raise ValueError("没有通过预检的商品，不能创建加购批次")
        profile = speed_profile_for(
            speed_mode,
            interval_ms=interval_ms,
            concurrency=concurrency,
        )
        now = datetime.now(UTC)
        batch = CartBatch(
            batch_id=uuid.uuid4().hex,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            pool_id=preview.pool_id,
            name=str(name).strip(),
            idempotency_key=str(idempotency_key).strip(),
            status=CartBatchStatus.AWAITING_CONFIRMATION,
            total_products=preview.total_products,
            products_per_batch=int(products_per_batch),
            quantity_per_product=int(quantity_per_product),
            sku_mode=CartSkuMode(sku_mode),
            specified_sku_id=str(specified_sku_id or "").strip(),
            out_of_stock_policy=OutOfStockPolicy(out_of_stock_policy),
            speed_mode=profile.mode,
            interval_ms=profile.interval_ms,
            concurrency=profile.concurrency,
            adapter_mode=preview.adapter_mode,
            created_by=str(actor_id),
            created_at=now,
            updated_at=now,
            summary={
                "ready_count": preview.ready_count,
                "skipped_count": preview.skipped_count,
                "failed_count": preview.failed_count,
                "estimated_amount_cents": preview.total_amount_cents,
            },
        )
        saved, created = self.repository.create_batch(
            batch,
            account_ids=account_ids,
        )
        if created:
            self._audit(
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                actor_id=str(actor_id),
                action="cart_batch_created",
                entity_type="cart_batch",
                entity_id=saved.batch_id,
                result="success",
                detail={
                    "pool_id": saved.pool_id,
                    "account_count": len(account_ids),
                    "ready_count": preview.ready_count,
                    "adapter_mode": saved.adapter_mode.value,
                },
            )
        return saved, created

    def confirm_batch(
        self,
        store,
        batch_id: str,
        *,
        permission_checker,
        actor_id: str,
    ) -> CartBatch:
        require_permission(
            permission_checker,
            "dongxiao_cart_execute",
        )
        batch = self.repository.get_batch(
            batch_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )
        if batch is None:
            raise ValueError("加购批次不存在")
        if batch.status in {
            CartBatchStatus.CANCELLED,
            CartBatchStatus.COMPLETED,
        }:
            raise ValueError("当前批次状态不允许执行")
        capability = self.capability(batch.adapter_mode)
        if not capability.can_add_to_cart or not capability.can_verify_cart:
            raise DesktopCartServiceError(capability.reason)
        preview = self.preview_batch(
            store,
            pool_id=batch.pool_id,
            quantity_per_product=batch.quantity_per_product,
            sku_mode=batch.sku_mode,
            specified_sku_id=batch.specified_sku_id,
            out_of_stock_policy=batch.out_of_stock_policy,
            products_per_batch=batch.products_per_batch,
            account_ids=[
                str(row["account_id"])
                for row in self.repository.list_assignments(batch_id)
            ],
            adapter_mode=batch.adapter_mode,
            permission_checker=permission_checker,
        )
        ready = [item for item in preview.items if item.status == "ready"]
        if not ready:
            raise ValueError("重新预检后没有可执行商品")
        now = datetime.now(UTC)
        assignments = self.repository.list_assignments(batch_id)
        for assignment in assignments:
            if str(assignment["task_id"] or ""):
                continue
            account = self.repository.get_account(
                str(assignment["account_id"]),
                store_id=int(store.id),
                store_app_id=str(store.app_id),
            )
            if account is None:
                raise DesktopCartServiceError("执行账号不存在")
            if account.adapter_mode is not batch.adapter_mode:
                raise DesktopCartServiceError("执行账号适配器与批次不一致")
            task_items = []
            for index, item in enumerate(ready):
                task_items.append(
                    {
                        "batch_id": batch.batch_id,
                        "account_id": account.account_id,
                        "product_id": item.product_id,
                        "sku_id": item.sku_id,
                        "product_title": item.title,
                        "specs": dict(item.specs),
                        "unit_price_cents": item.unit_price_cents,
                        "quantity": item.quantity,
                        "batch_no": batch_number_for(
                            index,
                            batch.products_per_batch,
                        ),
                        "store_name": str(getattr(store, "name", store.app_id)),
                        "interval_ms": batch.interval_ms,
                        "speed_mode": batch.speed_mode.value,
                        "concurrency": batch.concurrency,
                        "adapter_mode": batch.adapter_mode.value,
                    }
                )
            task, _created = self.task_service.create_desktop_cart_task(
                store,
                batch_id=batch.batch_id,
                account_id=account.account_id,
                items=task_items,
                idempotency_key=(
                    f"desktop-cart:{batch.batch_id}:{account.account_id}"
                ),
                actor_id=str(actor_id),
                execution_mode=(
                    "local_safe"
                    if batch.adapter_mode is CartAdapterMode.MOCK
                    else "platform_write"
                ),
                speed_mode=batch.speed_mode.value,
                interval_ms=batch.interval_ms,
                concurrency=batch.concurrency,
                adapter_mode=batch.adapter_mode.value,
                requested_at=now,
                max_attempts=1,
            )
            self.repository.bind_assignment_task(
                batch.batch_id,
                account.account_id,
                task.task_id,
                now=now,
            )
        queued = self.repository.update_batch(
            batch.batch_id,
            status=CartBatchStatus.QUEUED,
            confirmed_at=batch.confirmed_at or now,
            summary={
                **batch.summary,
                "confirmed_ready_count": len(ready),
                "confirmation_actor": str(actor_id),
            },
        )
        if self.scheduler is not None:
            self.scheduler.enqueue()
        self._audit(
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            actor_id=str(actor_id),
            action="cart_batch_confirmed",
            entity_type="cart_batch",
            entity_id=batch.batch_id,
            result="success",
            detail={
                "ready_count": len(ready),
                "account_count": len(assignments),
                "adapter_mode": batch.adapter_mode.value,
            },
        )
        return queued

    def sync_progress(self, batch_id: str) -> CartBatch:
        batch = self.repository.get_batch(batch_id)
        if batch is None:
            raise ValueError("加购批次不存在")
        assignments = self.repository.list_assignments(batch_id)
        statuses: list[str] = []
        for assignment in assignments:
            task_id = str(assignment["task_id"] or "")
            if not task_id:
                statuses.append("pending")
                continue
            task = self.catalog_repository.get_task(
                task_id,
                store_app_id=batch.store_app_id,
            )
            if task is None:
                statuses.append("unknown")
                continue
            items = self.catalog_repository.list_task_execution_items(
                task_id,
                store_app_id=batch.store_app_id,
                limit=100_000,
            )
            counts = {
                status: sum(item.status is status for item in items)
                for status in TaskItemStatus
            }
            assignment_status = {
                TaskStatus.PENDING: "pending",
                TaskStatus.PREPARING: "running",
                TaskStatus.RUNNING: "running",
                TaskStatus.PAUSED: "pending",
                TaskStatus.COMPLETED: "completed",
                TaskStatus.PARTIALLY_FAILED: "failed",
                TaskStatus.FAILED: "failed",
                TaskStatus.CANCELLED: "cancelled",
                TaskStatus.UNKNOWN: "unknown",
            }[task.status]
            statuses.append(assignment_status)
            self.repository.sync_assignment_progress(
                batch_id=batch_id,
                account_id=str(assignment["account_id"]),
                status=assignment_status,
                total_items=len(items),
                succeeded_items=counts[TaskItemStatus.SUCCEEDED],
                failed_items=counts[TaskItemStatus.FAILED],
                skipped_items=counts[TaskItemStatus.SKIPPED],
                unknown_items=counts[TaskItemStatus.UNKNOWN],
                started_at=task.started_at,
                finished_at=task.finished_at,
            )
        aggregate = aggregate_cart_results(statuses)
        if aggregate == "completed":
            final_status = CartBatchStatus.COMPLETED
        elif aggregate == "partially_failed":
            final_status = CartBatchStatus.PARTIALLY_FAILED
        elif aggregate == "failed":
            final_status = CartBatchStatus.FAILED
        elif aggregate == "unknown":
            final_status = CartBatchStatus.UNKNOWN
        elif any(status == "running" for status in statuses):
            final_status = CartBatchStatus.RUNNING
        else:
            final_status = batch.status
        if batch.status is CartBatchStatus.PAUSED:
            final_status = CartBatchStatus.PAUSED
        if batch.status is CartBatchStatus.CANCELLED:
            final_status = CartBatchStatus.CANCELLED
        return self.repository.update_batch(
            batch_id,
            status=final_status,
            started_at=(
                batch.started_at
                or (datetime.now(UTC) if final_status is CartBatchStatus.RUNNING else None)
            ),
            finished_at=(
                datetime.now(UTC)
                if final_status in {
                    CartBatchStatus.COMPLETED,
                    CartBatchStatus.PARTIALLY_FAILED,
                    CartBatchStatus.FAILED,
                    CartBatchStatus.UNKNOWN,
                    CartBatchStatus.CANCELLED,
                }
                else None
            ),
        )

    def pause_batch(self, store, batch_id: str, *, actor_id: str) -> CartBatch:
        for assignment in self.repository.list_assignments(batch_id):
            task_id = str(assignment["task_id"] or "")
            if not task_id:
                continue
            task = self.catalog_repository.get_task(
                task_id,
                store_app_id=str(store.app_id),
            )
            if task and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                try:
                    self.task_service.pause(store, task_id, actor_id=actor_id)
                except ValueError:
                    pass
        return self.repository.update_batch(
            batch_id,
            status=CartBatchStatus.PAUSED,
            error="用户暂停批次",
        )

    def resume_batch(self, store, batch_id: str, *, actor_id: str) -> CartBatch:
        for assignment in self.repository.list_assignments(batch_id):
            task_id = str(assignment["task_id"] or "")
            if not task_id:
                continue
            task = self.catalog_repository.get_task(
                task_id,
                store_app_id=str(store.app_id),
            )
            if task and task.status is TaskStatus.PAUSED:
                self.task_service.resume(store, task_id, actor_id=actor_id)
        if self.scheduler is not None:
            self.scheduler.enqueue()
        return self.repository.update_batch(
            batch_id,
            status=CartBatchStatus.QUEUED,
            error="",
        )

    def cancel_batch(self, store, batch_id: str, *, actor_id: str) -> CartBatch:
        for assignment in self.repository.list_assignments(batch_id):
            task_id = str(assignment["task_id"] or "")
            if not task_id:
                continue
            task = self.catalog_repository.get_task(
                task_id,
                store_app_id=str(store.app_id),
            )
            if task and task.status not in {
                TaskStatus.COMPLETED,
                TaskStatus.CANCELLED,
                TaskStatus.PARTIALLY_FAILED,
                TaskStatus.FAILED,
            }:
                try:
                    self.task_service.cancel(store, task_id, actor_id=actor_id)
                except ValueError:
                    pass
        return self.repository.update_batch(
            batch_id,
            status=CartBatchStatus.CANCELLED,
            finished_at=datetime.now(UTC),
            error="用户取消批次",
        )

    def create_pending_drafts(
        self,
        store,
        batch_id: str,
        *,
        actor_id: str,
    ) -> list[PendingOrderDraft]:
        batch = self.repository.get_batch(
            batch_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
        )
        if batch is None:
            raise ValueError("加购批次不存在")
        results = self.repository.list_results(
            batch_id=batch_id,
            store_id=int(store.id),
            status=CartResultStatus.SUCCEEDED,
            limit=100_000,
        )
        grouped: dict[str, list] = {}
        for result in results:
            if result.verification_source is not batch.adapter_mode:
                continue
            grouped.setdefault(result.account_id, []).append(result)
        drafts: list[PendingOrderDraft] = []
        now = datetime.now(UTC)
        for account_id, rows in grouped.items():
            draft = PendingOrderDraft(
                draft_id=uuid.uuid4().hex,
                batch_id=batch_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                store_name=str(getattr(store, "name", store.app_id)),
                account_id=account_id,
                status=PendingOrderStatus.AWAITING_APPROVAL,
                item_count=len(rows),
                total_amount_cents=sum(
                    row.unit_price_cents * row.quantity for row in rows
                ),
                created_at=now,
                updated_at=now,
                reason=(
                    "Mock 购物车核验成功，仅生成本地待付款草案；"
                    "不代表真实订单。"
                    if batch.adapter_mode is CartAdapterMode.MOCK
                    else "购物车核验成功，等待人工审批"
                ),
            )
            saved, _created = self.repository.create_pending_draft(draft)
            drafts.append(saved)
        if not drafts:
            raise ValueError("当前批次没有购物车核验成功的明细")
        self._audit(
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            actor_id=str(actor_id),
            action="cart_pending_drafts_created",
            entity_type="cart_batch",
            entity_id=batch_id,
            result="success",
            detail={"draft_count": len(drafts)},
        )
        return drafts

    def decide_pending_draft(
        self,
        store,
        draft_id: str,
        *,
        approve: bool,
        actor_id: str,
        permission_checker,
        reason: str = "",
    ) -> PendingOrderDraft:
        require_permission(
            permission_checker,
            "dongxiao_cart_order_approve",
        )
        drafts = self.repository.list_pending_drafts(
            store_id=int(store.id),
        )
        draft = next((item for item in drafts if item.draft_id == draft_id), None)
        if draft is None or draft.store_app_id != str(store.app_id):
            raise ValueError("待付款草案不存在或不属于当前店铺")
        if draft.status is not PendingOrderStatus.AWAITING_APPROVAL:
            raise ValueError("当前待付款草案不能再审批")
        saved = self.repository.update_pending_draft(
            draft_id,
            status=(
                PendingOrderStatus.APPROVED
                if approve
                else PendingOrderStatus.CANCELLED
            ),
            decided_by=str(actor_id),
            reason=reason or ("人工批准" if approve else "人工取消"),
        )
        self._audit(
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            actor_id=str(actor_id),
            action="cart_pending_draft_decided",
            entity_type="pending_order_draft",
            entity_id=draft_id,
            result="success",
            detail={"approved": approve, "reason": reason},
        )
        return saved

    def request_official_order_creation(self, draft_id: str) -> None:
        raise DesktopCartServiceError(
            "当前可确认的微信小店商家开放接口不支持由商家端创建"
            f"买家待付款订单，草案 {draft_id} 不会发送任何真实写请求。"
        )

    def _audit(
        self,
        *,
        store_id: int | None,
        store_app_id: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        result: str,
        detail: dict[str, object],
    ) -> None:
        self.catalog_repository.append_audit_event(
            AuditEvent(
                event_id=uuid.uuid4().hex,
                store_id=store_id,
                store_app_id=store_app_id,
                actor_id=str(actor_id),
                action=str(action),
                entity_type=str(entity_type),
                entity_id=str(entity_id),
                request_id="",
                result=AuditResult(str(result)),
                created_at=datetime.now(UTC),
                detail=dict(detail),
            )
        )
