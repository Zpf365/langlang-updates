from __future__ import annotations

import pytest

from weimi_manager.dongxiao.domain.enums import (
    AftersaleEvent,
    AftersaleStatus,
    ApprovalEvent,
    ApprovalStatus,
    OrderEvent,
    OrderStatus,
    TaskEvent,
    TaskStatus,
)
from weimi_manager.dongxiao.domain.state_machine import (
    InvalidStateTransition,
    UnknownResultRequiresVerification,
    allowed_task_transitions,
    transition_aftersale,
    transition_approval,
    transition_order,
    transition_task,
)


def test_task_state_machine_allows_declared_flow_only() -> None:
    assert (
        transition_task(TaskStatus.PENDING, TaskEvent.START_PREPARING)
        is TaskStatus.PREPARING
    )
    assert (
        transition_task(TaskStatus.PREPARING, TaskEvent.START_RUNNING)
        is TaskStatus.RUNNING
    )
    assert (
        transition_task(TaskStatus.RUNNING, TaskEvent.PAUSE)
        is TaskStatus.PAUSED
    )
    assert (
        transition_task(TaskStatus.PAUSED, TaskEvent.RESUME)
        is TaskStatus.PREPARING
    )
    assert (
        transition_task(TaskStatus.PREPARING, TaskEvent.START_RUNNING)
        is TaskStatus.RUNNING
    )
    assert (
        transition_task(TaskStatus.RUNNING, TaskEvent.COMPLETE)
        is TaskStatus.COMPLETED
    )

    with pytest.raises(InvalidStateTransition):
        transition_task(TaskStatus.PENDING, TaskEvent.COMPLETE)
    with pytest.raises(InvalidStateTransition):
        transition_task(TaskStatus.COMPLETED, TaskEvent.START_PREPARING)


def test_order_aftersale_and_approval_have_independent_transitions() -> None:
    assert (
        transition_order(
            OrderStatus.PENDING_PAYMENT,
            OrderEvent.MARK_PAID,
        )
        is OrderStatus.PAID
    )
    assert (
        transition_order(OrderStatus.PAID, OrderEvent.MARK_SHIPPED)
        is OrderStatus.SHIPPED
    )
    with pytest.raises(InvalidStateTransition):
        transition_order(OrderStatus.CANCELLED, OrderEvent.MARK_PAID)

    assert (
        transition_aftersale(
            AftersaleStatus.PENDING_REVIEW,
            AftersaleEvent.APPROVE,
        )
        is AftersaleStatus.APPROVED
    )
    with pytest.raises(InvalidStateTransition):
        transition_aftersale(
            AftersaleStatus.COMPLETED,
            AftersaleEvent.APPROVE,
        )

    assert (
        transition_approval(
            ApprovalStatus.PENDING,
            ApprovalEvent.APPROVE,
        )
        is ApprovalStatus.APPROVED
    )
    with pytest.raises(InvalidStateTransition):
        transition_approval(
            ApprovalStatus.REJECTED,
            ApprovalEvent.APPROVE,
        )


def test_every_required_task_state_exposes_explicit_destinations() -> None:
    assert set(TaskStatus) == {
        TaskStatus.PENDING,
        TaskStatus.PREPARING,
        TaskStatus.RUNNING,
        TaskStatus.PAUSED,
        TaskStatus.COMPLETED,
        TaskStatus.PARTIALLY_FAILED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.UNKNOWN,
    }
    assert TaskStatus.PREPARING in allowed_task_transitions(
        TaskStatus.PENDING
    )
    assert TaskStatus.RUNNING in allowed_task_transitions(
        TaskStatus.PREPARING
    )
    assert TaskStatus.PAUSED in allowed_task_transitions(
        TaskStatus.RUNNING
    )
    assert TaskStatus.COMPLETED in allowed_task_transitions(
        TaskStatus.RUNNING
    )
    assert TaskStatus.PARTIALLY_FAILED in allowed_task_transitions(
        TaskStatus.RUNNING
    )
    assert TaskStatus.FAILED in allowed_task_transitions(
        TaskStatus.RUNNING
    )
    assert TaskStatus.CANCELLED in allowed_task_transitions(
        TaskStatus.PAUSED
    )
    assert TaskStatus.UNKNOWN in allowed_task_transitions(
        TaskStatus.RUNNING
    )
    assert allowed_task_transitions(TaskStatus.COMPLETED) == ()


def test_unknown_task_recovery_requires_remote_verification() -> None:
    with pytest.raises(UnknownResultRequiresVerification):
        transition_task(
            TaskStatus.UNKNOWN,
            TaskEvent.RECOVER_UNKNOWN,
        )
    assert (
        transition_task(
            TaskStatus.UNKNOWN,
            TaskEvent.RECOVER_UNKNOWN,
            remote_result_verified=True,
        )
        is TaskStatus.PREPARING
    )
