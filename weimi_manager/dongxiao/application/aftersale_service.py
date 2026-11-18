from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

from ..domain.enums import (
    AftersaleStatus,
    ApprovalStatus,
    ApprovalSubjectType,
)
from ..domain.models import AftersaleCase, Approval
from ..domain.operations import (
    AftersaleAmountPreview,
    OperationSyncResult,
)
from ..domain.rules import (
    require_permission,
)
from ..infrastructure.platform import DongxiaoPlatformAdapter
from ..infrastructure.repository import DongxiaoRepository
from .task_service import DongxiaoTaskService

ProgressCallback = Callable[[str, int, int], None]


class DongxiaoAftersaleService:
    def __init__(
        self,
        repository: DongxiaoRepository,
        adapter: DongxiaoPlatformAdapter,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        self.clock = clock or (lambda: datetime.now(UTC))

    def sync_aftersales(
        self,
        store,
        *,
        start_time: int,
        end_time: int,
        time_field: str = "update",
        progress: ProgressCallback | None = None,
    ) -> OperationSyncResult:
        ids = self.adapter.list_aftersale_ids(
            store,
            int(start_time),
            int(end_time),
            time_field=time_field,
        )
        saved = 0
        errors: list[str] = []
        for index, case_id in enumerate(ids, 1):
            if progress:
                progress(
                    f"正在同步真实售后 {index}/{len(ids)}",
                    index,
                    len(ids),
                )
            try:
                self.sync_aftersale(store, str(case_id))
                saved += 1
            except Exception as exc:
                errors.append(f"{case_id}: {exc}")
        return OperationSyncResult(
            discovered=len(ids),
            saved=saved,
            failed=len(errors),
            errors=tuple(errors[:100]),
        )

    def sync_aftersale(self, store, case_id: str) -> AftersaleCase:
        detail = self.adapter.get_aftersale(store, str(case_id))
        if str(detail.get("after_sale_order_id") or "") != str(case_id):
            raise ValueError("售后详情返回的售后单号与请求不一致")
        return self.repository.upsert_aftersale_case(
            self._map_case(store, detail)
        )

    def list_cases(
        self,
        store,
        *,
        statuses: tuple[AftersaleStatus, ...] = (),
        limit: int = 500,
    ) -> list[AftersaleCase]:
        return self.repository.list_aftersale_cases(
            int(store.id),
            statuses=tuple(status.value for status in statuses),
            limit=limit,
        )

    def list_history(self, store_app_id: str) -> list[AftersaleCase]:
        return self.repository.list_aftersale_cases_by_store_app_id(
            str(store_app_id)
        )

    def preview_amount(
        self,
        case: AftersaleCase,
        *,
        approved_amount_cents: int,
    ) -> AftersaleAmountPreview:
        if isinstance(approved_amount_cents, bool) or not isinstance(
            approved_amount_cents,
            int,
        ):
            raise TypeError("审批退款金额必须是整数分")
        if approved_amount_cents < 0:
            raise ValueError("审批退款金额不能小于 0")
        if approved_amount_cents != case.requested_amount_cents:
            raise ValueError(
                "官方同意售后接口不支持指定部分退款金额，"
                "本地审批金额必须等于官方申请金额"
            )
        refunded, inflight, reserved_other = (
            self.repository.aftersale_refund_commitments(
                store_id=case.store_id,
                order_ref_id=case.order_ref_id,
                exclude_case_id=case.case_id,
            )
            if case.order_ref_id
            else (
                case.refunded_amount_cents,
                case.inflight_amount_cents,
                0,
            )
        )
        remaining = max(
            0,
            case.paid_amount_cents
            - refunded
            - inflight
            - reserved_other,
        )
        if approved_amount_cents > remaining:
            raise ValueError("审批退款金额不能超过订单剩余可退金额")
        return AftersaleAmountPreview(
            paid_amount_cents=case.paid_amount_cents,
            requested_amount_cents=case.requested_amount_cents,
            approved_amount_cents=approved_amount_cents,
            refunded_amount_cents=refunded,
            inflight_amount_cents=inflight,
            remaining_before_approval_cents=remaining,
            remaining_after_approval_cents=(
                remaining - approved_amount_cents
            ),
        )

    def create_approval(
        self,
        store,
        case_id: str,
        *,
        approved_amount_cents: int,
        requested_by: str,
        permission_checker,
        idempotency_key: str,
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> tuple[Approval, AftersaleCase]:
        require_permission(
            permission_checker,
            "dongxiao_aftersale_request",
        )
        case = self.repository.get_aftersale_case(
            int(store.id),
            str(case_id),
        )
        if case is None:
            raise ValueError("售后工单不存在")
        if case.source != "platform_api":
            raise ValueError("只有官方平台售后单可以进入审批")
        if case.platform_state is AftersaleStatus.UNKNOWN:
            raise ValueError("官方售后状态未知，禁止创建审批")
        if case.platform_state in {
            AftersaleStatus.COMPLETED,
            AftersaleStatus.REJECTED,
            AftersaleStatus.FAILED,
            AftersaleStatus.CANCELLED,
        }:
            raise ValueError("官方售后已经结束，不能创建审批")
        preview = self.preview_amount(
            case,
            approved_amount_cents=approved_amount_cents,
        )
        approval_id = hashlib.sha256(
            (
                f"{store.app_id}\0{case.case_id}\0"
                f"{idempotency_key}\0{preview.approved_amount_cents}"
            ).encode("utf-8")
        ).hexdigest()
        existing = self.repository.get_approval(
            approval_id,
            store_app_id=str(store.app_id),
        )
        if existing is not None:
            saved_case = self.repository.get_aftersale_case(
                int(store.id),
                case.case_id,
            )
            if saved_case is None:
                raise ValueError("审批关联的售后工单不存在")
            return existing, saved_case
        if case.status not in {
            AftersaleStatus.REQUESTED,
            AftersaleStatus.PENDING_REVIEW,
        }:
            raise ValueError("当前售后状态不允许新建退款审批")
        pending = self.repository.list_approvals(
            int(store.id),
            subject_type="aftersale",
            subject_id=case.case_id,
        )
        if any(
            approval.status is ApprovalStatus.PENDING
            for approval in pending
        ):
            raise ValueError("该售后已有待处理审批")
        now = self.clock()
        if case.status is AftersaleStatus.REQUESTED:
            case = self.repository.transition_aftersale(
                case.case_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                event="start_review",
                actor_id=requested_by,
                reason="创建退款审批",
            )
        case = self.repository.upsert_aftersale_case(
            replace(
                case,
                status=AftersaleStatus.PENDING_REVIEW,
                approved_amount_cents=preview.approved_amount_cents,
                reason=str(reason or case.reason),
                updated_at=now,
            )
        )
        approval = Approval(
            approval_id=approval_id,
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            subject_type=ApprovalSubjectType.AFTERSALE,
            subject_id=case.case_id,
            status=ApprovalStatus.PENDING,
            requested_by=str(requested_by),
            requested_at=now,
            decision_reason=str(reason),
            expires_at=expires_at,
        )
        try:
            self.repository.create_approval(approval)
        except sqlite3.IntegrityError as exc:
            if "approvals" in str(exc).lower() or "unique" in str(exc).lower():
                raise ValueError("该售后已有待处理审批") from exc
            raise
        return approval, case

    def decide_approval(
        self,
        store,
        approval_id: str,
        *,
        approved: bool,
        actor_id: str,
        reason: str = "",
        permission_checker,
    ) -> tuple[Approval, AftersaleCase]:
        require_permission(
            permission_checker,
            "dongxiao_aftersale_approve",
        )
        approval = self.repository.get_approval(
            approval_id,
            store_app_id=str(store.app_id),
        )
        if approval is None:
            raise ValueError("审批记录不存在")
        now = self.clock()
        if (
            approval.status is ApprovalStatus.PENDING
            and approval.expires_at is not None
            and now > approval.expires_at
        ):
            self.repository.transition_approval(
                approval.approval_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                event="expire",
                actor_id=actor_id,
                reason="审批已超过有效期",
            )
            raise ValueError("审批已过期，不能批准或驳回")
        target_status = (
            ApprovalStatus.APPROVED
            if approved
            else ApprovalStatus.REJECTED
        )
        if approval.status is not ApprovalStatus.PENDING:
            if approval.status is not target_status:
                raise ValueError("审批已经作出不同决定")
        else:
            approval = self.repository.transition_approval(
                approval.approval_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                event="approve" if approved else "reject",
                actor_id=actor_id,
                reason=reason,
            )
        case = self.repository.get_aftersale_case(
            int(store.id),
            approval.subject_id,
        )
        if case is None:
            raise ValueError("审批关联的售后工单不存在")
        target_case = (
            AftersaleStatus.APPROVED
            if approved
            else AftersaleStatus.REJECTED
        )
        if case.status is not target_case:
            case = self.repository.transition_aftersale(
                case.case_id,
                store_id=int(store.id),
                store_app_id=str(store.app_id),
                event="approve" if approved else "reject",
                actor_id=actor_id,
                reason=reason,
            )
        return approval, case

    def list_approvals(
        self,
        store,
        *,
        case_id: str = "",
    ) -> list[Approval]:
        return self.repository.list_approvals(
            int(store.id),
            subject_type="aftersale",
            subject_id=str(case_id),
        )

    def create_decision_task(
        self,
        store,
        case_id: str,
        *,
        action: str,
        actor_id: str,
        permission_checker,
        idempotency_key: str,
        address_id: str = "",
        accept_type: int | None = None,
        reject_reason_type: int = 0,
        reject_reason: str = "",
    ):
        require_permission(
            permission_checker,
            "dongxiao_aftersale_execute",
        )
        case = self.repository.get_aftersale_case(
            int(store.id),
            str(case_id),
        )
        if case is None:
            raise ValueError("售后工单不存在")
        if case.status is not AftersaleStatus.APPROVED:
            raise ValueError("售后工单尚未通过金额审批")
        if case.platform_state is AftersaleStatus.UNKNOWN:
            raise ValueError("官方售后状态未知，禁止平台执行")
        if case.platform_state in {
            AftersaleStatus.COMPLETED,
            AftersaleStatus.REJECTED,
            AftersaleStatus.FAILED,
            AftersaleStatus.CANCELLED,
        }:
            raise ValueError("官方售后已经结束，不能执行平台写操作")
        approvals = self.repository.list_approvals(
            int(store.id),
            subject_type="aftersale",
            subject_id=case.case_id,
        )
        if not any(
            approval.status is ApprovalStatus.APPROVED
            for approval in approvals
        ):
            raise ValueError("没有已通过的售后审批")
        self.preview_amount(
            case,
            approved_amount_cents=case.approved_amount_cents,
        )
        return DongxiaoTaskService(
            self.repository
        ).create_aftersale_decision_task(
            store,
            case.case_id,
            action=action,
            approved_amount_cents=case.approved_amount_cents,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            address_id=address_id,
            accept_type=accept_type,
            reject_reason_type=reject_reason_type,
            reject_reason=reject_reason,
        )

    def _map_case(self, store, detail: dict) -> AftersaleCase:
        case_id = str(detail.get("after_sale_order_id") or "").strip()
        order_id = str(detail.get("order_id") or "").strip()
        existing = self.repository.get_aftersale_case(
            int(store.id),
            case_id,
        )
        order = self.repository.get_order_record(
            int(store.id),
            order_id,
        )
        refund_info = detail.get("refund_info")
        requested = (
            int(refund_info.get("amount") or 0)
            if isinstance(refund_info, dict)
            else 0
        )
        created_at = _timestamp(detail.get("create_time"))
        updated_at = _timestamp(detail.get("update_time"))
        now = self.clock()
        mapped_status = _map_aftersale_status(
            str(detail.get("status") or "")
        )
        workflow_status = _next_workflow_status(
            existing.status if existing else None,
            mapped_status,
        )
        amount_changed = (
            existing is not None
            and existing.requested_amount_cents != requested
        )
        if amount_changed and workflow_status in {
            AftersaleStatus.APPROVED,
            AftersaleStatus.PENDING_REVIEW,
        }:
            workflow_status = AftersaleStatus.PENDING_REVIEW
        refunded = (
            requested
            if mapped_status is AftersaleStatus.COMPLETED
            else (existing.refunded_amount_cents if existing else 0)
        )
        inflight = (
            requested
            if mapped_status in {
                AftersaleStatus.PROCESSING,
                AftersaleStatus.APPROVED,
            }
            else 0
        )
        return AftersaleCase(
            case_id=hashlib.sha256(
                f"{store.app_id}\0{case_id}".encode("utf-8")
            ).hexdigest(),
            store_id=int(store.id),
            store_app_id=str(store.app_id),
            platform_case_id=case_id,
            order_ref_id=order.order_ref_id if order else "",
            status=workflow_status,
            requested_amount_cents=requested,
            approved_amount_cents=(
                0
                if amount_changed
                else min(
                    requested,
                    existing.approved_amount_cents if existing else 0,
                )
            ),
            idempotency_key=f"platform:{store.app_id}:{case_id}",
            created_at=created_at or now,
            updated_at=updated_at or now,
            source="platform_api",
            reason=existing.reason if existing else "",
            platform_status=str(detail.get("status") or ""),
            platform_reason=str(
                detail.get("reason_text") or detail.get("reason") or ""
            ),
            platform_order_id=order_id,
            platform_state=mapped_status,
            platform_state_updated_at=now,
            after_sale_type=str(detail.get("type") or ""),
            paid_amount_cents=(
                order.total_amount_cents if order is not None else 0
            ),
            refunded_amount_cents=refunded,
            inflight_amount_cents=inflight,
            deadline=_timestamp(detail.get("deadline")),
            complete_time=_timestamp(detail.get("complete_time")),
            details={
                "sub_type": str(detail.get("sub_type") or ""),
                "product_info": detail.get("product_info") or {},
                "refund_resp": detail.get("refund_resp") or {},
            },
            last_synced_at=now,
        )

def _timestamp(value) -> datetime | None:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


def _map_aftersale_status(value: str) -> AftersaleStatus:
    status = str(value or "").upper()
    if status == "USER_CANCELD":
        return AftersaleStatus.CANCELLED
    if status in {
        "MERCHANT_REFUND_SUCCESS",
        "MERCHANT_RETURN_SUCCESS",
        "MERCHANT_EXCHANGE_SUCCESS",
        "RESHIP_SUCCESS",
    }:
        return AftersaleStatus.COMPLETED
    if status in {
        "MERCHANT_REJECT_REFUND",
        "MERCHANT_REJECT_RETURN",
        "MERCHANT_REJECT_EXCHANGE",
        "MERCHANT_REJECT_RESHIP",
        "RETURN_CLOSED",
    }:
        return AftersaleStatus.REJECTED
    if status in {
        "MERCHANT_FAIL",
        "PLATFORM_REFUND_FAIL",
        "MERCHANT_REFUND_RETRY_FAIL",
    }:
        return AftersaleStatus.FAILED
    if status in {
        "MERCHANT_PROCESSING",
        "USER_WAIT_CONFIRM",
        "USER_WAIT_CONFIRM_UPDATE",
        "USER_WAIT_HANDLE_MERCHANT_AFTER_SALE",
    }:
        return AftersaleStatus.PENDING_REVIEW
    if status in {
        "PLATFORM_REFUNDING",
        "PLATFORM_PROCESSING",
        "MERCHANT_OVERDUE_REFUND",
        "WAIT_PACKAGE_INTERCEPT",
        "USER_WAIT_RETURN",
        "MERCHANT_WAIT_RECEIPT",
        "USER_WAIT_RECEIPT",
        "MERCHANT_WAIT_RESHIP",
        "MERCHANT_RESHIP_OVERDUE",
    }:
        return AftersaleStatus.PROCESSING
    return AftersaleStatus.UNKNOWN


def _next_workflow_status(
    current: AftersaleStatus | None,
    platform_state: AftersaleStatus,
) -> AftersaleStatus:
    if platform_state is AftersaleStatus.UNKNOWN:
        return current or AftersaleStatus.UNKNOWN
    if platform_state is AftersaleStatus.COMPLETED:
        return AftersaleStatus.COMPLETED
    if platform_state is AftersaleStatus.REJECTED:
        return AftersaleStatus.REJECTED
    if platform_state is AftersaleStatus.FAILED:
        return AftersaleStatus.FAILED
    if platform_state is AftersaleStatus.CANCELLED:
        return AftersaleStatus.CANCELLED
    if platform_state is AftersaleStatus.PROCESSING:
        if current in {AftersaleStatus.APPROVED, AftersaleStatus.PENDING_REVIEW}:
            return AftersaleStatus.PROCESSING
        return current or AftersaleStatus.PROCESSING
    if current is not None:
        return current
    return (
        AftersaleStatus.PENDING_REVIEW
        if platform_state is AftersaleStatus.PENDING_REVIEW
        else platform_state
    )
