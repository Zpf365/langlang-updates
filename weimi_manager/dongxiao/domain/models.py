from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterable, Mapping

from .enums import (
    AftersaleStatus,
    ApprovalStatus,
    ApprovalSubjectType,
    AuditResult,
    OrderStatus,
    OrderVerificationSource,
    ProductPoolStatus,
    ProductState,
    TaskAttemptStatus,
    TaskItemStatus,
    TaskStatus,
)
from .rules import (
    OrderTrustError,
    ensure_utc,
    sanitize_audit_detail,
    validate_money_cents,
)


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _store_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("店铺 ID 必须为正整数")
    return value


def _quantity(
    value: object,
    field_name: str,
    *,
    allow_zero: bool = True,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name}必须是整数")
    if value < (0 if allow_zero else 1):
        raise ValueError(f"{field_name}不能小于 {0 if allow_zero else 1}")
    return value


def _specs(
    value: Mapping[str, object] | Iterable[tuple[str, object]],
) -> tuple[tuple[str, str], ...]:
    pairs = value.items() if isinstance(value, Mapping) else value
    normalized = tuple(
        sorted(
            (str(key).strip(), str(item).strip())
            for key, item in pairs
            if str(key).strip() or str(item).strip()
        )
    )
    if len({key for key, _value in normalized}) != len(normalized):
        raise ValueError("SKU 规格名称不能重复")
    return normalized


@dataclass(frozen=True, slots=True)
class Product:
    store_id: int
    store_app_id: str
    product_id: str
    title: str
    state: ProductState
    min_price_cents: int
    stock_total: int
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(
            self,
            "store_app_id",
            _required_text(self.store_app_id, "店铺 AppID"),
        )
        object.__setattr__(
            self,
            "product_id",
            _required_text(self.product_id, "商品 ID"),
        )
        object.__setattr__(self, "title", _required_text(self.title, "商品标题"))
        object.__setattr__(self, "state", ProductState(self.state))
        object.__setattr__(
            self,
            "min_price_cents",
            validate_money_cents(self.min_price_cents, field="商品最低价"),
        )
        object.__setattr__(
            self,
            "stock_total",
            _quantity(self.stock_total, "商品库存"),
        )
        object.__setattr__(
            self,
            "observed_at",
            ensure_utc(self.observed_at, field="商品观测时间"),
        )


@dataclass(frozen=True, slots=True)
class Sku:
    store_id: int
    store_app_id: str
    product_id: str
    sku_id: str
    specs: tuple[tuple[str, str], ...] = ()
    price_cents: int = 0
    stock: int = 0
    available: bool = True
    purchase_min: int = 1
    purchase_max: int | None = None
    is_default: bool = False
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(
            self,
            "store_app_id",
            _required_text(self.store_app_id, "店铺 AppID"),
        )
        object.__setattr__(
            self,
            "product_id",
            _required_text(self.product_id, "商品 ID"),
        )
        object.__setattr__(self, "sku_id", _required_text(self.sku_id, "SKU ID"))
        object.__setattr__(self, "specs", _specs(self.specs))
        object.__setattr__(
            self,
            "price_cents",
            validate_money_cents(
                self.price_cents,
                field="SKU 价格",
                allow_zero=False,
            ),
        )
        object.__setattr__(self, "stock", _quantity(self.stock, "SKU 库存"))
        object.__setattr__(self, "available", bool(self.available))
        minimum = _quantity(
            self.purchase_min,
            "SKU 起购数量",
            allow_zero=False,
        )
        object.__setattr__(self, "purchase_min", minimum)
        if self.purchase_max is not None:
            maximum = _quantity(
                self.purchase_max,
                "SKU 限购数量",
                allow_zero=False,
            )
            if maximum < minimum:
                raise ValueError("SKU 限购数量不能小于起购数量")
            object.__setattr__(self, "purchase_max", maximum)
        object.__setattr__(self, "is_default", bool(self.is_default))
        object.__setattr__(
            self,
            "observed_at",
            ensure_utc(self.observed_at, field="SKU 观测时间"),
        )


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    snapshot_id: str
    store_id: int
    store_app_id: str
    product_id: str
    sku_id: str
    title: str
    price_cents: int
    stock: int
    available: bool
    source: str
    observed_at: datetime
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _required_text(self.snapshot_id, "快照 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(self, "product_id", _required_text(self.product_id, "商品 ID"))
        object.__setattr__(self, "sku_id", str(self.sku_id or "").strip())
        object.__setattr__(self, "title", _required_text(self.title, "商品标题"))
        object.__setattr__(
            self,
            "price_cents",
            validate_money_cents(self.price_cents, field="快照价格"),
        )
        object.__setattr__(self, "stock", _quantity(self.stock, "快照库存"))
        object.__setattr__(self, "available", bool(self.available))
        object.__setattr__(self, "source", _required_text(self.source, "快照来源"))
        object.__setattr__(
            self,
            "observed_at",
            ensure_utc(self.observed_at, field="快照观测时间"),
        )
        sanitized = sanitize_audit_detail(dict(self.details))
        object.__setattr__(self, "details", sanitized)


@dataclass(frozen=True, slots=True)
class ProductPool:
    pool_id: str
    store_id: int
    store_app_id: str
    name: str
    status: ProductPoolStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "pool_id", _required_text(self.pool_id, "商品池 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(self, "name", _required_text(self.name, "商品池名称"))
        object.__setattr__(self, "status", ProductPoolStatus(self.status))
        object.__setattr__(
            self,
            "idempotency_key",
            _required_text(self.idempotency_key, "商品池幂等键"),
        )
        created = ensure_utc(self.created_at, field="商品池创建时间")
        updated = ensure_utc(self.updated_at, field="商品池更新时间")
        if updated < created:
            raise ValueError("商品池更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)


@dataclass(frozen=True, slots=True)
class ProductPoolItem:
    item_id: str
    pool_id: str
    store_id: int
    store_app_id: str
    product_id: str
    sku_id: str = ""
    position: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _required_text(self.item_id, "商品池明细 ID"))
        object.__setattr__(self, "pool_id", _required_text(self.pool_id, "商品池 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(self, "product_id", _required_text(self.product_id, "商品 ID"))
        object.__setattr__(self, "sku_id", str(self.sku_id or "").strip())
        object.__setattr__(self, "position", _quantity(self.position, "商品排序位置"))
        object.__setattr__(
            self,
            "created_at",
            ensure_utc(self.created_at, field="商品池明细创建时间"),
        )


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    store_id: int
    store_app_id: str
    task_type: str
    idempotency_key: str
    status: TaskStatus
    total_items: int
    succeeded_items: int
    failed_items: int
    created_at: datetime
    updated_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _required_text(self.task_id, "任务 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(self, "task_type", _required_text(self.task_type, "任务类型"))
        object.__setattr__(
            self,
            "idempotency_key",
            _required_text(self.idempotency_key, "任务幂等键"),
        )
        object.__setattr__(self, "status", TaskStatus(self.status))
        object.__setattr__(self, "total_items", _quantity(self.total_items, "任务总项数"))
        object.__setattr__(
            self,
            "succeeded_items",
            _quantity(self.succeeded_items, "任务成功项数"),
        )
        object.__setattr__(
            self,
            "failed_items",
            _quantity(self.failed_items, "任务失败项数"),
        )
        if self.succeeded_items + self.failed_items > self.total_items:
            raise ValueError("任务成功与失败项数之和不能超过总项数")
        created = ensure_utc(self.created_at, field="任务创建时间")
        updated = ensure_utc(self.updated_at, field="任务更新时间")
        if updated < created:
            raise ValueError("任务更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "metadata", sanitize_audit_detail(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class TaskItem:
    item_id: str
    task_id: str
    store_id: int
    store_app_id: str
    product_id: str
    sku_id: str
    status: TaskItemStatus
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _required_text(self.item_id, "任务明细 ID"))
        object.__setattr__(self, "task_id", _required_text(self.task_id, "任务 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(self, "product_id", _required_text(self.product_id, "商品 ID"))
        object.__setattr__(self, "sku_id", str(self.sku_id or "").strip())
        object.__setattr__(self, "status", TaskItemStatus(self.status))
        object.__setattr__(
            self,
            "attempt_count",
            _quantity(self.attempt_count, "尝试次数"),
        )
        created = ensure_utc(self.created_at, field="任务明细创建时间")
        updated = ensure_utc(self.updated_at, field="任务明细更新时间")
        if updated < created:
            raise ValueError("任务明细更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "reason", str(self.reason or "")[:1000])


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    attempt_id: str
    task_id: str
    item_id: str
    store_id: int
    store_app_id: str
    attempt_no: int
    status: TaskAttemptStatus
    request_id: str
    started_at: datetime
    result: Mapping[str, object] = field(default_factory=dict)
    finished_at: datetime | None = None
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _required_text(self.attempt_id, "尝试 ID"))
        object.__setattr__(self, "task_id", _required_text(self.task_id, "任务 ID"))
        object.__setattr__(self, "item_id", _required_text(self.item_id, "任务明细 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(
            self,
            "attempt_no",
            _quantity(self.attempt_no, "尝试序号", allow_zero=False),
        )
        object.__setattr__(self, "status", TaskAttemptStatus(self.status))
        object.__setattr__(self, "request_id", str(self.request_id or "").strip())
        started = ensure_utc(self.started_at, field="尝试开始时间")
        finished = (
            ensure_utc(self.finished_at, field="尝试结束时间")
            if self.finished_at is not None
            else None
        )
        if finished is not None and finished < started:
            raise ValueError("尝试结束时间不能早于开始时间")
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)
        object.__setattr__(self, "error_code", str(self.error_code or "")[:200])
        object.__setattr__(self, "error_message", str(self.error_message or "")[:1000])
        object.__setattr__(self, "result", sanitize_audit_detail(dict(self.result)))


@dataclass(frozen=True, slots=True)
class RealOrderReference:
    order_ref_id: str
    store_id: int
    store_app_id: str
    platform_order_id: str
    status: OrderStatus
    verified: bool
    source: OrderVerificationSource
    verified_at: datetime | None
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_ref_id", _required_text(self.order_ref_id, "订单引用 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(
            self,
            "platform_order_id",
            _required_text(self.platform_order_id, "平台订单号"),
        )
        object.__setattr__(self, "status", OrderStatus(self.status))
        source = OrderVerificationSource(self.source)
        verified = bool(self.verified)
        if verified and source is not OrderVerificationSource.PLATFORM_API:
            raise OrderTrustError(
                "只有平台官方接口返回的订单才能标记为真实已验证"
            )
        if verified != (self.verified_at is not None):
            raise ValueError("订单核验状态与核验时间不一致")
        object.__setattr__(self, "verified", verified)
        object.__setattr__(self, "source", source)
        verified_at = (
            ensure_utc(self.verified_at, field="订单核验时间")
            if self.verified_at is not None
            else None
        )
        object.__setattr__(self, "verified_at", verified_at)
        object.__setattr__(
            self,
            "created_at",
            ensure_utc(self.created_at, field="订单引用创建时间"),
        )


@dataclass(frozen=True, slots=True)
class OrderStatusSnapshot:
    snapshot_id: str
    order_ref_id: str
    store_id: int
    store_app_id: str
    platform_order_id: str
    status: OrderStatus
    trusted: bool
    source: OrderVerificationSource
    observed_at: datetime
    request_id: str = ""
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _required_text(self.snapshot_id, "订单快照 ID"))
        object.__setattr__(self, "order_ref_id", _required_text(self.order_ref_id, "订单引用 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(
            self,
            "platform_order_id",
            _required_text(self.platform_order_id, "平台订单号"),
        )
        object.__setattr__(self, "status", OrderStatus(self.status))
        source = OrderVerificationSource(self.source)
        trusted = bool(self.trusted)
        if trusted and source is not OrderVerificationSource.PLATFORM_API:
            raise OrderTrustError("非官方来源的订单快照不能标记为可信")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "trusted", trusted)
        object.__setattr__(
            self,
            "observed_at",
            ensure_utc(self.observed_at, field="订单快照时间"),
        )
        object.__setattr__(self, "request_id", str(self.request_id or "")[:200])
        object.__setattr__(self, "details", sanitize_audit_detail(dict(self.details)))


@dataclass(frozen=True, slots=True)
class AftersaleCase:
    case_id: str
    store_id: int
    store_app_id: str
    platform_case_id: str
    status: AftersaleStatus
    requested_amount_cents: int
    approved_amount_cents: int
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    order_ref_id: str = ""
    source: str = "unknown"
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _required_text(self.case_id, "售后工单 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(
            self,
            "platform_case_id",
            _required_text(self.platform_case_id, "平台售后单号"),
        )
        object.__setattr__(self, "status", AftersaleStatus(self.status))
        object.__setattr__(
            self,
            "requested_amount_cents",
            validate_money_cents(self.requested_amount_cents, field="申请退款金额"),
        )
        object.__setattr__(
            self,
            "approved_amount_cents",
            validate_money_cents(self.approved_amount_cents, field="同意退款金额"),
        )
        if self.approved_amount_cents > self.requested_amount_cents:
            raise ValueError("同意退款金额不能超过申请退款金额")
        object.__setattr__(
            self,
            "idempotency_key",
            _required_text(self.idempotency_key, "售后幂等键"),
        )
        created = ensure_utc(self.created_at, field="售后创建时间")
        updated = ensure_utc(self.updated_at, field="售后更新时间")
        if updated < created:
            raise ValueError("售后更新时间不能早于创建时间")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "order_ref_id", str(self.order_ref_id or "").strip())
        object.__setattr__(self, "source", _required_text(self.source, "售后来源"))
        object.__setattr__(self, "reason", str(self.reason or "")[:1000])


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: str
    store_id: int
    store_app_id: str
    subject_type: ApprovalSubjectType
    subject_id: str
    status: ApprovalStatus
    requested_by: str
    requested_at: datetime
    decided_by: str = ""
    decision_reason: str = ""
    decided_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "approval_id", _required_text(self.approval_id, "审批 ID"))
        object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", _required_text(self.store_app_id, "店铺 AppID"))
        object.__setattr__(self, "subject_type", ApprovalSubjectType(self.subject_type))
        object.__setattr__(self, "subject_id", _required_text(self.subject_id, "审批对象 ID"))
        object.__setattr__(self, "status", ApprovalStatus(self.status))
        object.__setattr__(self, "requested_by", _required_text(self.requested_by, "申请人"))
        requested_at = ensure_utc(self.requested_at, field="审批申请时间")
        decided_at = (
            ensure_utc(self.decided_at, field="审批决定时间")
            if self.decided_at is not None
            else None
        )
        expires_at = (
            ensure_utc(self.expires_at, field="审批过期时间")
            if self.expires_at is not None
            else None
        )
        decided_by = str(self.decided_by or "").strip()
        if self.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            if not decided_by or decided_at is None:
                raise ValueError("已处理的审批必须记录决定人和决定时间")
        elif decided_at is not None and not decided_by:
            raise ValueError("审批决定时间必须伴随决定人")
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "decided_at", decided_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "decided_by", decided_by[:200])
        object.__setattr__(
            self,
            "decision_reason",
            str(self.decision_reason or "")[:1000],
        )


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    store_id: int | None
    store_app_id: str
    actor_id: str
    action: str
    entity_type: str
    entity_id: str
    request_id: str
    result: AuditResult
    created_at: datetime
    detail: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "审计事件 ID"))
        if self.store_id is not None:
            object.__setattr__(self, "store_id", _store_id(self.store_id))
        object.__setattr__(self, "store_app_id", str(self.store_app_id or "").strip())
        object.__setattr__(self, "actor_id", str(self.actor_id or "").strip()[:200])
        object.__setattr__(self, "action", _required_text(self.action, "审计动作"))
        object.__setattr__(self, "entity_type", _required_text(self.entity_type, "审计对象类型"))
        object.__setattr__(self, "entity_id", _required_text(self.entity_id, "审计对象 ID"))
        object.__setattr__(self, "request_id", str(self.request_id or "").strip()[:200])
        object.__setattr__(self, "result", AuditResult(self.result))
        object.__setattr__(
            self,
            "created_at",
            ensure_utc(self.created_at, field="审计时间"),
        )
        object.__setattr__(self, "detail", sanitize_audit_detail(dict(self.detail)))
