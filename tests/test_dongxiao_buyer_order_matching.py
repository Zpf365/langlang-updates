from __future__ import annotations

from datetime import UTC, datetime, timedelta

from weimi_manager.dongxiao.buyer_flow.matching import (
    CandidateOrder,
    CandidateOrderItem,
    ExpectedOrderItem,
    OrderMatchState,
    match_candidate_orders,
)


def test_exact_order_match_requires_single_full_candidate() -> None:
    now = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    expected = (
        ExpectedOrderItem("p1", "s1", 1, 1000),
        ExpectedOrderItem("p2", "s2", 2, 2000),
    )
    candidate = CandidateOrder(
        "order-1",
        "pending_payment",
        5000,
        now,
        (
            CandidateOrderItem("p1", "s1", 1, 1000),
            CandidateOrderItem("p2", "s2", 2, 2000),
        ),
    )
    result = match_candidate_orders(
        expected,
        (candidate,),
        not_before=now - timedelta(minutes=1),
        now=now,
    )
    assert result.state is OrderMatchState.EXACT


def test_waiting_split_ambiguous_and_not_found_are_distinct() -> None:
    now = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    expected = (
        ExpectedOrderItem("p1", "s1", 1, 1000),
        ExpectedOrderItem("p2", "s2", 1, 2000),
    )
    assert match_candidate_orders(
        expected,
        (),
        not_before=now - timedelta(minutes=2),
        now=now,
    ).state is OrderMatchState.WAITING
    assert match_candidate_orders(
        expected,
        (),
        not_before=now - timedelta(minutes=11),
        now=now,
    ).state is OrderMatchState.NOT_FOUND

    split = (
        CandidateOrder(
            "order-split-1",
            "pending_payment",
            1000,
            now,
            (CandidateOrderItem("p1", "s1", 1, 1000),),
        ),
        CandidateOrder(
            "order-split-2",
            "pending_payment",
            2000,
            now,
            (CandidateOrderItem("p2", "s2", 1, 2000),),
        ),
    )
    assert match_candidate_orders(
        expected,
        split,
        not_before=now,
        now=now,
    ).state is OrderMatchState.SPLIT

    ambiguous = (
        CandidateOrder(
            "order-a",
            "pending_payment",
            1000,
            now,
            (CandidateOrderItem("p1", "s1", 1, 1000),),
        ),
        CandidateOrder(
            "order-b",
            "pending_payment",
            1000,
            now,
            (CandidateOrderItem("p1", "s1", 1, 1000),),
        ),
    )
    assert match_candidate_orders(
        expected,
        ambiguous,
        not_before=now,
        now=now,
    ).state is OrderMatchState.AMBIGUOUS
