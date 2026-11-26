from __future__ import annotations

import pytest

from weimi_manager.sales_related.state_machine import (
    InvalidTaskTransition,
    SalesEvent,
    SalesTaskStatus,
    transition_task,
)


def test_complete_order_path() -> None:
    status = SalesTaskStatus.PENDING
    for event in (
        SalesEvent.IDENTIFY_PRODUCT,
        SalesEvent.CONFIRM_SKU,
        SalesEvent.VERIFY_CHECKOUT,
        SalesEvent.REQUEST_USER_CONFIRMATION,
        SalesEvent.REQUEST_SUBMISSION,
        SalesEvent.SUBMISSION_CONFIRMED,
        SalesEvent.MARK_PAID,
        SalesEvent.MARK_SHIPPED,
        SalesEvent.COMPLETE,
    ):
        status = transition_task(status, event)
    assert status == SalesTaskStatus.COMPLETED


def test_unknown_submission_requires_verification_before_resume() -> None:
    unknown = transition_task(
        SalesTaskStatus.AWAITING_USER_CONFIRMATION,
        SalesEvent.REQUEST_SUBMISSION,
    )
    unknown = transition_task(unknown, SalesEvent.SUBMISSION_UNKNOWN)
    assert unknown == SalesTaskStatus.UNKNOWN
    assert transition_task(
        unknown,
        SalesEvent.RESUME_AFTER_VERIFICATION,
    ) == SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION


def test_verified_not_created_returns_to_checkout_not_direct_retry() -> None:
    target = transition_task(
        SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION,
        SalesEvent.SUBMISSION_NOT_FOUND,
    )
    assert target == SalesTaskStatus.CHECKOUT_VERIFIED


def test_terminal_status_rejects_further_transition() -> None:
    with pytest.raises(InvalidTaskTransition):
        transition_task(
            SalesTaskStatus.COMPLETED,
            SalesEvent.MARK_PAID,
        )
