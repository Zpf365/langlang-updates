from __future__ import annotations

from types import SimpleNamespace

import requests

from ...wechat_api import (
    WechatApiError,
    WechatNetworkError,
    WechatTimeoutError,
)
from ..application.aftersale_service import DongxiaoAftersaleService
from ..application.order_service import DongxiaoOrderService
from ..domain.enums import (
    ProductState,
)
from ..domain.models import TaskExecutionItem
from ..domain.operations import TaskExecutionError
from ..domain.rules import (
    SkuSelectionFailure,
    SkuSelectionMode,
    select_sku,
)
from ..infrastructure.repository import (
    DongxiaoRepository,
    EntityNotFound,
)


class LocalSkuValidationExecutor:
    def __init__(self, repository: DongxiaoRepository) -> None:
        self.repository = repository

    def execute(self, item: TaskExecutionItem) -> dict[str, object]:
        if item.subject_type != "product":
            raise TaskExecutionError(
                "unsupported_subject",
                "SKU 校验器不支持当前任务对象",
            )
        snapshot = self.repository.catalog_snapshot_for_products(
            item.store_id,
            [item.product_id],
        ).get(item.product_id)
        if snapshot is None:
            raise TaskExecutionError(
                "product_missing",
                "商品不在本地当前目录中",
            )
        product, candidates = snapshot
        return self._validate(item, product, candidates)

    def execute_batch(
        self,
        items: list[TaskExecutionItem],
    ) -> dict[str, tuple[dict[str, object] | None, TaskExecutionError | None]]:
        if any(item.subject_type != "product" for item in items):
            return {
                item.item_id: (
                    None,
                    TaskExecutionError(
                        "unsupported_subject",
                        "SKU 校验器不支持当前任务对象",
                    ),
                )
                for item in items
            }
        snapshots = self.repository.catalog_snapshot_for_products(
            items[0].store_id,
            [item.product_id for item in items],
        )
        result = {}
        for item in items:
            snapshot = snapshots.get(item.product_id)
            if snapshot is None:
                result[item.item_id] = (
                    None,
                    TaskExecutionError(
                        "product_missing",
                        "商品不在本地当前目录中",
                    ),
                )
                continue
            product, candidates = snapshot
            try:
                validated = self._validate(item, product, candidates)
            except TaskExecutionError as exc:
                result[item.item_id] = (None, exc)
            else:
                result[item.item_id] = (validated, None)
        return result

    @staticmethod
    def _validate(
        item: TaskExecutionItem,
        product,
        candidates,
    ) -> dict[str, object]:
        if product.state is not ProductState.ACTIVE:
            raise TaskExecutionError(
                "product_inactive",
                "商品当前不是上架状态",
            )
        quantity = int(item.payload.get("quantity") or 1)
        selection = select_sku(
            store_id=item.store_id,
            product_id=item.product_id,
            candidates=candidates,
            quantity=quantity,
            mode=(
                SkuSelectionMode.SPECIFIED
                if item.sku_id
                else SkuSelectionMode.ANY
            ),
            specified_sku_id=item.sku_id,
            product_state=product.state,
        )
        if selection.selected is None:
            failure = selection.failure or SkuSelectionFailure.INCOMPLETE_DATA
            raise TaskExecutionError(
                failure.value,
                selection.reason or "SKU 规则校验未通过",
            )
        selected = selection.selected
        return {
            "product_id": selected.product_id,
            "sku_id": selected.sku_id,
            "quantity": selection.quantity,
            "price_cents": selected.price_cents,
            "stock": selected.stock,
        }

    def verify(self, item: TaskExecutionItem) -> dict[str, object]:
        del item
        raise TaskExecutionError(
            "verification_unavailable",
            "本地 SKU 校验任务没有待核验的外部结果",
        )


class CompositeTaskExecutor:
    def __init__(
        self,
        repository: DongxiaoRepository,
        *,
        order_service: DongxiaoOrderService,
        aftersale_service: DongxiaoAftersaleService,
        aftersale_decision_executor: "AftersaleDecisionExecutor | None" = None,
    ) -> None:
        self.repository = repository
        self.local_sku = LocalSkuValidationExecutor(repository)
        self.order_service = order_service
        self.aftersale_service = aftersale_service
        self.aftersale_decision_executor = aftersale_decision_executor

    def execute(self, item: TaskExecutionItem) -> dict[str, object]:
        store = self._store(item)
        try:
            if item.subject_type == "product":
                return self.local_sku.execute(item)
            if item.subject_type == "order":
                record = self.order_service.sync_order(
                    store,
                    item.subject_id,
                )
                return {
                    "order_id": record.platform_order_id,
                    "status": record.status.value,
                    "total_amount_cents": record.total_amount_cents,
                }
            if item.subject_type == "aftersale":
                if item.payload.get("action") in {"accept", "reject"}:
                    if self.aftersale_decision_executor is None:
                        raise TaskExecutionError(
                            "platform_write_unavailable",
                            "当前未配置官方售后执行适配器",
                        )
                    return self.aftersale_decision_executor.execute(item)
                case = self.aftersale_service.sync_aftersale(
                    store,
                    item.subject_id,
                )
                return {
                    "case_id": case.platform_case_id,
                    "status": case.status.value,
                    "requested_amount_cents": case.requested_amount_cents,
                }
        except TaskExecutionError:
            raise
        except (
            requests.Timeout,
            requests.ConnectionError,
            WechatTimeoutError,
            WechatNetworkError,
        ) as exc:
            raise TaskExecutionError(
                "network_error",
                str(exc),
                retryable=True,
            ) from exc
        except WechatApiError as exc:
            if exc.code in {-1, 45011}:
                raise TaskExecutionError(
                    f"wechat_{exc.code}",
                    str(exc),
                    retryable=True,
                ) from exc
            raise TaskExecutionError(
                f"wechat_{exc.code or 'unknown'}",
                str(exc),
            ) from exc
        except (PermissionError, ValueError) as exc:
            raise TaskExecutionError(
                "data_or_permission_error",
                str(exc),
            ) from exc
        raise TaskExecutionError(
            "unsupported_subject",
            "不支持的任务对象类型",
        )

    def execute_batch(self, items: list[TaskExecutionItem]):
        if all(item.subject_type == "product" for item in items):
            return self.local_sku.execute_batch(items)
        return {}

    def verify(self, item: TaskExecutionItem) -> dict[str, object]:
        store = self._store(item)
        if item.subject_type == "order":
            record = self.order_service.sync_order(
                store,
                item.subject_id,
            )
            return {
                "verified": True,
                "order_id": record.platform_order_id,
                "status": record.status.value,
            }
        if item.subject_type == "aftersale":
            if (
                item.payload.get("action") in {"accept", "reject"}
                and self.aftersale_decision_executor is not None
            ):
                return self.aftersale_decision_executor.verify(item)
            case = self.aftersale_service.sync_aftersale(
                store,
                item.subject_id,
            )
            return {
                "verified": True,
                "case_id": case.platform_case_id,
                "status": case.status.value,
            }
        return self.local_sku.verify(item)

    def _store(self, item: TaskExecutionItem):
        if item.store_id is None:
            raise TaskExecutionError(
                "store_deleted",
                "店铺已删除，禁止继续平台或业务执行",
            )
        try:
            identity = self.repository.get_store_identity(
                item.store_id,
                item.store_app_id,
            )
        except EntityNotFound as exc:
            raise TaskExecutionError(
                "store_deleted",
                "店铺已删除，禁止继续平台或业务执行",
            ) from exc
        return SimpleNamespace(
            id=identity["id"],
            name=identity["name"],
            app_id=identity["app_id"],
        )


class AftersaleDecisionExecutor:
    def __init__(
        self,
        repository: DongxiaoRepository,
        adapter,
        aftersale_service: DongxiaoAftersaleService,
        *,
        platform_write_gate=None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        self.aftersale_service = aftersale_service
        self.platform_write_gate = platform_write_gate

    def execute(self, item: TaskExecutionItem) -> dict[str, object]:
        if item.subject_type != "aftersale":
            raise TaskExecutionError(
                "unsupported_subject",
                "售后执行器不支持当前任务对象",
            )
        store = self._store(item)
        case = self.repository.get_aftersale_case(
            item.store_id,
            item.subject_id,
        )
        if case is None:
            raise TaskExecutionError(
                "aftersale_missing",
                "售后工单不存在",
            )
        approvals = self.repository.list_approvals(
            item.store_id,
            subject_type="aftersale",
            subject_id=case.case_id,
        )
        if not any(
            approval.status.value == "approved"
            for approval in approvals
        ):
            raise TaskExecutionError(
                "approval_required",
                "售后执行缺少已通过审批",
            )
        amount = int(item.payload.get("approved_amount_cents") or 0)
        if amount != case.approved_amount_cents:
            raise TaskExecutionError(
                "approval_amount_mismatch",
                "任务金额与已审批金额不一致",
            )
        self.aftersale_service.preview_amount(
            case,
            approved_amount_cents=amount,
        )
        if (
            self.platform_write_gate is not None
            and not self.platform_write_gate(item.store_app_id)
        ):
            raise TaskExecutionError(
                "platform_write_not_allowed",
                "当前会话或店铺没有有效的真实只读验收，禁止发送平台写请求",
            )
        action = str(item.payload.get("action") or "")
        previous_status = case.platform_status
        target_statuses = _target_platform_statuses(
            action,
            case.after_sale_type,
            (
                int(item.payload["accept_type"])
                if item.payload.get("accept_type") is not None
                else None
            ),
        )
        if not target_statuses:
            raise TaskExecutionError(
                "unknown_target_state",
                "无法根据售后类型确定平台操作目标状态",
                unknown=True,
            )
        try:
            if action == "accept":
                self.adapter.accept_aftersale(
                    store,
                    case.platform_case_id,
                    address_id=str(item.payload.get("address_id") or ""),
                    accept_type=(
                        int(item.payload["accept_type"])
                        if item.payload.get("accept_type") is not None
                        else None
                    ),
                )
            elif action == "reject":
                self.adapter.reject_aftersale(
                    store,
                    case.platform_case_id,
                    reject_reason_type=int(
                        item.payload.get("reject_reason_type") or 0
                    ),
                    reject_reason=str(
                        item.payload.get("reject_reason") or ""
                    ),
                )
            else:
                raise TaskExecutionError(
                    "unsupported_action",
                    "不支持的售后执行动作",
                )
        except TaskExecutionError:
            raise
        except WechatApiError as exc:
            definitive_codes = {
                10020000,
                10020001,
                10021041,
                10021043,
                10021050,
                10021057,
                10021060,
                10021062,
                10021065,
                10021102,
                10021103,
            }
            if exc.code in definitive_codes:
                raise TaskExecutionError(
                    f"aftersale_rejected_{exc.code}",
                    str(exc),
                ) from exc
            raise TaskExecutionError(
                "aftersale_write_unknown",
                f"平台写请求结果未知，必须先核验：{exc}",
                unknown=True,
            ) from exc
        except Exception as exc:
            raise TaskExecutionError(
                "aftersale_write_unknown",
                f"平台写请求结果未知，必须先核验：{exc}",
                unknown=True,
            ) from exc
        try:
            verified = self.aftersale_service.sync_aftersale(
                store,
                case.platform_case_id,
            )
        except Exception as exc:
            raise TaskExecutionError(
                "aftersale_verify_failed",
                f"平台写后回查失败，结果未知：{exc}",
                unknown=True,
            ) from exc
        if verified.platform_status == previous_status:
            raise TaskExecutionError(
                "aftersale_result_unchanged",
                "平台写请求后状态未变化，需人工核验",
                unknown=True,
            )
        if verified.platform_status not in target_statuses:
            raise TaskExecutionError(
                "aftersale_target_state_mismatch",
                "平台状态变化但未达到当前操作的目标状态",
                unknown=True,
            )
        return {
            "case_id": verified.platform_case_id,
            "platform_status": verified.platform_status,
            "requested_amount_cents": verified.requested_amount_cents,
        }

    def _store(self, item: TaskExecutionItem):
        if item.store_id is None:
            raise TaskExecutionError(
                "store_deleted",
                "禁止对已删除店铺执行平台写操作",
            )
        try:
            identity = self.repository.get_store_identity(
                item.store_id,
                item.store_app_id,
            )
        except EntityNotFound as exc:
            raise TaskExecutionError(
                "store_deleted",
                "店铺已删除，禁止继续平台或业务执行",
            ) from exc
        return SimpleNamespace(
            id=identity["id"],
            name=identity["name"],
            app_id=identity["app_id"],
        )

    def verify(self, item: TaskExecutionItem) -> dict[str, object]:
        if item.subject_type != "aftersale":
            raise TaskExecutionError(
                "unsupported_subject",
                "售后执行器不支持当前任务对象",
            )
        store = self._store(item)
        verified = self.aftersale_service.sync_aftersale(
            store,
            item.subject_id,
        )
        initial_status = str(
            item.payload.get("initial_platform_status") or ""
        )
        if verified.platform_status == initial_status:
            raise TaskExecutionError(
                "aftersale_result_still_unknown",
                "重新核验后平台状态未变化，继续维持结果未知",
                unknown=True,
            )
        target_statuses = _target_platform_statuses(
            str(item.payload.get("action") or ""),
            verified.after_sale_type,
            (
                int(item.payload["accept_type"])
                if item.payload.get("accept_type") is not None
                else None
            ),
        )
        if not target_statuses:
            raise TaskExecutionError(
                "unknown_target_state",
                "无法根据售后类型确定平台操作目标状态",
                unknown=True,
            )
        if verified.platform_status not in target_statuses:
            raise TaskExecutionError(
                "aftersale_target_state_mismatch",
                "远端状态变化但未达到当前操作的目标状态",
                unknown=True,
            )
        return {
            "verified": True,
            "case_id": verified.platform_case_id,
            "platform_status": verified.platform_status,
        }


def _target_platform_statuses(
    action: str,
    after_sale_type: str,
    accept_type: int | None,
) -> frozenset[str]:
    normalized_action = str(action or "").upper()
    normalized_type = str(after_sale_type or "").upper()
    if normalized_action == "REJECT":
        return frozenset(
            {
                "MERCHANT_REJECT_REFUND",
                "MERCHANT_REJECT_RETURN",
                "MERCHANT_REJECT_EXCHANGE",
                "MERCHANT_REJECT_RESHIP",
            }
        )
    if normalized_action != "ACCEPT":
        return frozenset()
    refund_states = {
        "PLATFORM_REFUNDING",
        "MERCHANT_REFUND_SUCCESS",
        "MERCHANT_RETURN_SUCCESS",
    }
    if accept_type in {2, 3}:
        return frozenset(refund_states)
    if normalized_type == "REFUND":
        return frozenset(refund_states)
    if normalized_type == "RETURN":
        return frozenset(
            {
                "USER_WAIT_RETURN",
                "MERCHANT_WAIT_RECEIPT",
                "PLATFORM_REFUNDING",
                "MERCHANT_RETURN_SUCCESS",
            }
        )
    if normalized_type == "EXCHANGE":
        return frozenset(
            {
                "USER_WAIT_RECEIPT",
                "MERCHANT_EXCHANGE_SUCCESS",
            }
        )
    return frozenset()
