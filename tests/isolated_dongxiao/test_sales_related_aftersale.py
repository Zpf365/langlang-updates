from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from weimi_manager.sales_related.aftersale_rules import (
    InvalidRefundTransition,
    RefundEvent,
    RefundRequest,
    RefundStatus,
    WhitelistPolicy,
    evaluate_refund_request,
    transition_refund,
)


def policy(**overrides) -> WhitelistPolicy:
    now = datetime.now(UTC)
    values = {
        "enabled": True,
        "store_id": 1,
        "buyer_identifier_type": "openid",
        "buyer_identifier": "openid-1",
        "allowed_after_sale_types": frozenset({"refund"}),
        "single_refund_limit_cents": 5000,
        "daily_refund_limit_cents": 10000,
        "monthly_refund_limit_cents": 30000,
        "valid_from": now - timedelta(days=1),
        "valid_to": now + timedelta(days=1),
        "allowed_order_ids": frozenset({"ORDER-1"}),
        "emergency_stop": False,
    }
    values.update(overrides)
    return WhitelistPolicy(**values)


def request(**overrides) -> RefundRequest:
    values = {
        "store_id": 1,
        "buyer_identifier_type": "openid",
        "buyer_identifier": "openid-1",
        "after_sale_type": "refund",
        "order_id": "ORDER-1",
        "amount_cents": 1000,
        "order_refundable_cents": 5000,
        "already_refunded_cents": 0,
        "processing_refund_cents": 0,
        "daily_refunded_cents": 0,
        "monthly_refunded_cents": 0,
        "requested_at": datetime.now(UTC),
        "idempotency_key": "refund-1",
    }
    values.update(overrides)
    return RefundRequest(**values)


def test_simulation_approval_only_when_all_rules_match() -> None:
    decision = evaluate_refund_request(policy(), request())
    assert decision.approved_for_simulation
    assert decision.reason_code == "matched"


def test_cross_store_and_unverified_identity_require_safe_outcome() -> None:
    cross_store = evaluate_refund_request(policy(), request(store_id=2))
    assert cross_store.outcome == "reject"
    nickname = evaluate_refund_request(
        policy(buyer_identifier_type="nickname"),
        request(buyer_identifier_type="nickname", buyer_identifier="张三"),
    )
    assert nickname.outcome == "manual_review"


def test_remaining_refund_balance_includes_processing_amount() -> None:
    decision = evaluate_refund_request(
        policy(),
        request(
            amount_cents=2500,
            order_refundable_cents=5000,
            already_refunded_cents=2000,
            processing_refund_cents=1000,
        ),
    )
    assert decision.outcome == "reject"
    assert decision.max_refundable_cents == 2000


def test_daily_and_monthly_limits_route_to_manual_review() -> None:
    daily = evaluate_refund_request(
        policy(),
        request(daily_refunded_cents=9500),
    )
    assert daily.outcome == "manual_review"
    assert daily.reason_code == "daily_limit"
    monthly = evaluate_refund_request(
        policy(),
        request(monthly_refunded_cents=29500),
    )
    assert monthly.outcome == "manual_review"
    assert monthly.reason_code == "monthly_limit"


def test_emergency_stop_blocks_simulation() -> None:
    decision = evaluate_refund_request(
        policy(emergency_stop=True),
        request(),
    )
    assert not decision.approved_for_simulation
    assert decision.reason_code == "emergency_stop"


def test_refund_states_keep_payment_acceptance_separate_from_success() -> None:
    approved = transition_refund(
        RefundStatus.REQUESTED,
        RefundEvent.APPROVE,
    )
    accepted = transition_refund(
        approved,
        RefundEvent.PAYMENT_ACCEPT,
    )
    assert accepted == RefundStatus.PAYMENT_ACCEPTED
    assert transition_refund(
        accepted,
        RefundEvent.SUCCEED,
    ) == RefundStatus.REFUNDED
    with pytest.raises(InvalidRefundTransition):
        transition_refund(RefundStatus.REFUNDED, RefundEvent.FAIL)
