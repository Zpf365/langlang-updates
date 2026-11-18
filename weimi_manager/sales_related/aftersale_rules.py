from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .constants import VERIFIED_BUYER_IDENTIFIER_TYPES


class RefundStatus(StrEnum):
    REQUESTED = "requested"
    MERCHANT_APPROVED = "merchant_approved"
    PAYMENT_ACCEPTED = "payment_accepted"
    REFUNDED = "refunded"
    PROCESSING = "processing"
    REJECTED = "rejected"
    FAILED = "failed"


class RefundEvent(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    PAYMENT_ACCEPT = "payment_accept"
    SUCCEED = "succeed"
    FAIL = "fail"


class InvalidRefundTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WhitelistPolicy:
    enabled: bool
    store_id: int
    buyer_identifier_type: str
    buyer_identifier: str
    allowed_after_sale_types: frozenset[str]
    single_refund_limit_cents: int
    daily_refund_limit_cents: int
    monthly_refund_limit_cents: int
    valid_from: datetime
    valid_to: datetime | None = None
    allowed_order_ids: frozenset[str] = field(default_factory=frozenset)
    emergency_stop: bool = False


@dataclass(frozen=True, slots=True)
class RefundRequest:
    store_id: int
    buyer_identifier_type: str
    buyer_identifier: str
    after_sale_type: str
    order_id: str
    amount_cents: int
    order_refundable_cents: int
    already_refunded_cents: int = 0
    processing_refund_cents: int = 0
    daily_refunded_cents: int = 0
    monthly_refunded_cents: int = 0
    requested_at: datetime = field(default_factory=datetime.now)
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class RefundDecision:
    outcome: str
    reason_code: str
    reason: str
    max_refundable_cents: int

    @property
    def approved_for_simulation(self) -> bool:
        return self.outcome == "approve_simulation"


def evaluate_refund_request(
    policy: WhitelistPolicy,
    request: RefundRequest,
) -> RefundDecision:
    """Evaluate a whitelist rule without calling any real refund interface."""
    if request.amount_cents < 0:
        raise ValueError("退款金额不能为负数")
    remaining = max(
        0,
        int(request.order_refundable_cents)
        - int(request.already_refunded_cents)
        - int(request.processing_refund_cents),
    )
    if policy.emergency_stop:
        return RefundDecision(
            "manual_review",
            "emergency_stop",
            "售后自动规则已紧急停用，请人工审核",
            remaining,
        )
    if not policy.enabled:
        return RefundDecision(
            "manual_review",
            "whitelist_disabled",
            "买家白名单未启用",
            remaining,
        )
    if policy.store_id != request.store_id:
        return RefundDecision(
            "reject",
            "cross_store_identity",
            "买家身份不属于当前店铺",
            remaining,
        )
    identifier_type = str(policy.buyer_identifier_type or "").casefold()
    if (
        identifier_type not in VERIFIED_BUYER_IDENTIFIER_TYPES
        or identifier_type != request.buyer_identifier_type.casefold()
        or not policy.buyer_identifier
        or policy.buyer_identifier != request.buyer_identifier
    ):
        return RefundDecision(
            "manual_review",
            "identity_unverified",
            "买家唯一标识缺失、冲突或未经验证",
            remaining,
        )
    if request.requested_at < policy.valid_from or (
        policy.valid_to is not None
        and request.requested_at > policy.valid_to
    ):
        return RefundDecision(
            "reject",
            "outside_validity",
            "买家白名单当前不在有效期内",
            remaining,
        )
    if request.after_sale_type not in policy.allowed_after_sale_types:
        return RefundDecision(
            "reject",
            "type_not_allowed",
            "该售后类型未在买家白名单中授权",
            remaining,
        )
    if policy.allowed_order_ids and request.order_id not in policy.allowed_order_ids:
        return RefundDecision(
            "reject",
            "order_not_allowed",
            "订单不在允许的售后范围内",
            remaining,
        )
    if request.amount_cents <= 0:
        return RefundDecision(
            "reject",
            "amount_invalid",
            "退款金额必须大于 0",
            remaining,
        )
    if request.amount_cents > remaining:
        return RefundDecision(
            "reject",
            "over_refundable",
            "退款金额超过订单当前可退余额",
            remaining,
        )
    if request.amount_cents > policy.single_refund_limit_cents:
        return RefundDecision(
            "manual_review",
            "single_limit",
            "退款金额超过单笔上限，请人工审核",
            remaining,
        )
    if (
        request.daily_refunded_cents + request.amount_cents
        > policy.daily_refund_limit_cents
    ):
        return RefundDecision(
            "manual_review",
            "daily_limit",
            "退款金额超过每日累计上限，请人工审核",
            remaining,
        )
    if (
        request.monthly_refunded_cents + request.amount_cents
        > policy.monthly_refund_limit_cents
    ):
        return RefundDecision(
            "manual_review",
            "monthly_limit",
            "退款金额超过每月累计上限，请人工审核",
            remaining,
        )
    return RefundDecision(
        "approve_simulation",
        "matched",
        "规则预检通过，仅允许进入模拟审核",
        remaining,
    )


_REFUND_TRANSITIONS = {
    (RefundStatus.REQUESTED, RefundEvent.APPROVE):
        RefundStatus.MERCHANT_APPROVED,
    (RefundStatus.REQUESTED, RefundEvent.REJECT):
        RefundStatus.REJECTED,
    (RefundStatus.MERCHANT_APPROVED, RefundEvent.PAYMENT_ACCEPT):
        RefundStatus.PAYMENT_ACCEPTED,
    (RefundStatus.PAYMENT_ACCEPTED, RefundEvent.SUCCEED):
        RefundStatus.REFUNDED,
    (RefundStatus.PAYMENT_ACCEPTED, RefundEvent.FAIL):
        RefundStatus.FAILED,
}


def transition_refund(
    current: RefundStatus | str,
    event: RefundEvent | str,
) -> RefundStatus:
    try:
        status = RefundStatus(current)
        refund_event = RefundEvent(event)
    except ValueError as exc:
        raise InvalidRefundTransition("未知退款状态或事件") from exc
    target = _REFUND_TRANSITIONS.get((status, refund_event))
    if target is None:
        raise InvalidRefundTransition(
            f"退款不能从“{status.value}”执行“{refund_event.value}”"
        )
    return target
