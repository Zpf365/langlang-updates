from __future__ import annotations

from .enums import (
    AftersaleEvent,
    AftersaleStatus,
    ApprovalEvent,
    ApprovalStatus,
    OrderEvent,
    OrderStatus,
    TaskEvent,
    TaskStatus,
)


class InvalidStateTransition(ValueError):
    pass


_TASK_TRANSITIONS: dict[tuple[TaskStatus, TaskEvent], TaskStatus] = {
    (TaskStatus.PENDING, TaskEvent.START_PREPARING):
        TaskStatus.PREPARING,
    (TaskStatus.PENDING, TaskEvent.CANCEL):
        TaskStatus.CANCELLED,
    (TaskStatus.PREPARING, TaskEvent.START_RUNNING):
        TaskStatus.RUNNING,
    (TaskStatus.PREPARING, TaskEvent.PAUSE):
        TaskStatus.PAUSED,
    (TaskStatus.PREPARING, TaskEvent.FAIL):
        TaskStatus.FAILED,
    (TaskStatus.PREPARING, TaskEvent.CANCEL):
        TaskStatus.CANCELLED,
    (TaskStatus.PREPARING, TaskEvent.MARK_UNKNOWN):
        TaskStatus.UNKNOWN,
    (TaskStatus.RUNNING, TaskEvent.PAUSE):
        TaskStatus.PAUSED,
    (TaskStatus.RUNNING, TaskEvent.COMPLETE):
        TaskStatus.COMPLETED,
    (TaskStatus.RUNNING, TaskEvent.PARTIAL_FAILURE):
        TaskStatus.PARTIALLY_FAILED,
    (TaskStatus.RUNNING, TaskEvent.FAIL):
        TaskStatus.FAILED,
    (TaskStatus.RUNNING, TaskEvent.CANCEL):
        TaskStatus.CANCELLED,
    (TaskStatus.RUNNING, TaskEvent.MARK_UNKNOWN):
        TaskStatus.UNKNOWN,
    (TaskStatus.PAUSED, TaskEvent.RESUME):
        TaskStatus.PREPARING,
    (TaskStatus.PAUSED, TaskEvent.CANCEL):
        TaskStatus.CANCELLED,
    (TaskStatus.PAUSED, TaskEvent.MARK_UNKNOWN):
        TaskStatus.UNKNOWN,
    (TaskStatus.PARTIALLY_FAILED, TaskEvent.RESUME):
        TaskStatus.PREPARING,
    (TaskStatus.PARTIALLY_FAILED, TaskEvent.CANCEL):
        TaskStatus.CANCELLED,
    (TaskStatus.UNKNOWN, TaskEvent.RECOVER_UNKNOWN):
        TaskStatus.PREPARING,
    (TaskStatus.UNKNOWN, TaskEvent.CANCEL):
        TaskStatus.CANCELLED,
}


_ORDER_TRANSITIONS: dict[tuple[OrderStatus, OrderEvent], OrderStatus] = {
    (OrderStatus.PENDING_PAYMENT, OrderEvent.MARK_PAID):
        OrderStatus.PAID,
    (OrderStatus.PENDING_PAYMENT, OrderEvent.MARK_CANCELLED):
        OrderStatus.CANCELLED,
    (OrderStatus.PENDING_PAYMENT, OrderEvent.MARK_UNKNOWN):
        OrderStatus.UNKNOWN,
    (OrderStatus.PAID, OrderEvent.MARK_SHIPPED):
        OrderStatus.SHIPPED,
    (OrderStatus.PAID, OrderEvent.START_REFUND):
        OrderStatus.REFUNDING,
    (OrderStatus.PAID, OrderEvent.MARK_UNKNOWN):
        OrderStatus.UNKNOWN,
    (OrderStatus.SHIPPED, OrderEvent.MARK_COMPLETED):
        OrderStatus.COMPLETED,
    (OrderStatus.SHIPPED, OrderEvent.START_REFUND):
        OrderStatus.REFUNDING,
    (OrderStatus.SHIPPED, OrderEvent.MARK_UNKNOWN):
        OrderStatus.UNKNOWN,
    (OrderStatus.COMPLETED, OrderEvent.START_REFUND):
        OrderStatus.REFUNDING,
    (OrderStatus.COMPLETED, OrderEvent.MARK_UNKNOWN):
        OrderStatus.UNKNOWN,
    (OrderStatus.REFUNDING, OrderEvent.MARK_PAID):
        OrderStatus.PAID,
    (OrderStatus.REFUNDING, OrderEvent.MARK_SHIPPED):
        OrderStatus.SHIPPED,
    (OrderStatus.REFUNDING, OrderEvent.MARK_COMPLETED):
        OrderStatus.COMPLETED,
    (OrderStatus.REFUNDING, OrderEvent.MARK_REFUNDED):
        OrderStatus.REFUNDED,
    (OrderStatus.REFUNDING, OrderEvent.MARK_UNKNOWN):
        OrderStatus.UNKNOWN,
}
_ORDER_EVENT_BY_TARGET = {
    OrderStatus.PENDING_PAYMENT: OrderEvent.MARK_PENDING_PAYMENT,
    OrderStatus.PAID: OrderEvent.MARK_PAID,
    OrderStatus.SHIPPED: OrderEvent.MARK_SHIPPED,
    OrderStatus.COMPLETED: OrderEvent.MARK_COMPLETED,
    OrderStatus.CANCELLED: OrderEvent.MARK_CANCELLED,
    OrderStatus.REFUNDING: OrderEvent.START_REFUND,
    OrderStatus.REFUNDED: OrderEvent.MARK_REFUNDED,
    OrderStatus.UNKNOWN: OrderEvent.MARK_UNKNOWN,
}
for _target, _event in _ORDER_EVENT_BY_TARGET.items():
    _ORDER_TRANSITIONS[(OrderStatus.UNKNOWN, _event)] = _target


_AFTERSALE_TRANSITIONS: dict[
    tuple[AftersaleStatus, AftersaleEvent],
    AftersaleStatus,
] = {
    (AftersaleStatus.REQUESTED, AftersaleEvent.START_REVIEW):
        AftersaleStatus.PENDING_REVIEW,
    (AftersaleStatus.REQUESTED, AftersaleEvent.CANCEL):
        AftersaleStatus.CANCELLED,
    (AftersaleStatus.REQUESTED, AftersaleEvent.MARK_UNKNOWN):
        AftersaleStatus.UNKNOWN,
    (AftersaleStatus.PENDING_REVIEW, AftersaleEvent.APPROVE):
        AftersaleStatus.APPROVED,
    (AftersaleStatus.PENDING_REVIEW, AftersaleEvent.REJECT):
        AftersaleStatus.REJECTED,
    (AftersaleStatus.PENDING_REVIEW, AftersaleEvent.CANCEL):
        AftersaleStatus.CANCELLED,
    (AftersaleStatus.PENDING_REVIEW, AftersaleEvent.MARK_UNKNOWN):
        AftersaleStatus.UNKNOWN,
    (AftersaleStatus.APPROVED, AftersaleEvent.START_PROCESSING):
        AftersaleStatus.PROCESSING,
    (AftersaleStatus.APPROVED, AftersaleEvent.CANCEL):
        AftersaleStatus.CANCELLED,
    (AftersaleStatus.APPROVED, AftersaleEvent.FAIL):
        AftersaleStatus.FAILED,
    (AftersaleStatus.APPROVED, AftersaleEvent.MARK_UNKNOWN):
        AftersaleStatus.UNKNOWN,
    (AftersaleStatus.PROCESSING, AftersaleEvent.COMPLETE):
        AftersaleStatus.COMPLETED,
    (AftersaleStatus.PROCESSING, AftersaleEvent.FAIL):
        AftersaleStatus.FAILED,
    (AftersaleStatus.PROCESSING, AftersaleEvent.MARK_UNKNOWN):
        AftersaleStatus.UNKNOWN,
    (AftersaleStatus.FAILED, AftersaleEvent.RETRY):
        AftersaleStatus.PROCESSING,
    (AftersaleStatus.FAILED, AftersaleEvent.MARK_UNKNOWN):
        AftersaleStatus.UNKNOWN,
}
_AFTERSALE_EVENT_BY_TARGET = {
    AftersaleStatus.REQUESTED: AftersaleEvent.MARK_REQUESTED,
    AftersaleStatus.PENDING_REVIEW: AftersaleEvent.START_REVIEW,
    AftersaleStatus.APPROVED: AftersaleEvent.APPROVE,
    AftersaleStatus.REJECTED: AftersaleEvent.REJECT,
    AftersaleStatus.PROCESSING: AftersaleEvent.START_PROCESSING,
    AftersaleStatus.COMPLETED: AftersaleEvent.COMPLETE,
    AftersaleStatus.FAILED: AftersaleEvent.FAIL,
    AftersaleStatus.CANCELLED: AftersaleEvent.CANCEL,
    AftersaleStatus.UNKNOWN: AftersaleEvent.MARK_UNKNOWN,
}
for _target, _event in _AFTERSALE_EVENT_BY_TARGET.items():
    if (_target, _event) not in _AFTERSALE_TRANSITIONS:
        _AFTERSALE_TRANSITIONS[
            (AftersaleStatus.UNKNOWN, _event)
        ] = _target


_APPROVAL_TRANSITIONS: dict[
    tuple[ApprovalStatus, ApprovalEvent],
    ApprovalStatus,
] = {
    (ApprovalStatus.PENDING, ApprovalEvent.APPROVE):
        ApprovalStatus.APPROVED,
    (ApprovalStatus.PENDING, ApprovalEvent.REJECT):
        ApprovalStatus.REJECTED,
    (ApprovalStatus.PENDING, ApprovalEvent.EXPIRE):
        ApprovalStatus.EXPIRED,
    (ApprovalStatus.PENDING, ApprovalEvent.CANCEL):
        ApprovalStatus.CANCELLED,
}


def _transition(
    current,
    event,
    *,
    status_type,
    event_type,
    transitions,
    label: str,
):
    try:
        current_status = status_type(current)
    except ValueError as exc:
        raise InvalidStateTransition(f"未知{label}状态：{current}") from exc
    try:
        state_event = event_type(event)
    except ValueError as exc:
        raise InvalidStateTransition(f"未知{label}事件：{event}") from exc
    target = transitions.get((current_status, state_event))
    if target is None:
        raise InvalidStateTransition(
            f"{label}不能从“{current_status.value}”执行“{state_event.value}”"
        )
    return target


def transition_task(
    current: TaskStatus | str,
    event: TaskEvent | str,
) -> TaskStatus:
    return _transition(
        current,
        event,
        status_type=TaskStatus,
        event_type=TaskEvent,
        transitions=_TASK_TRANSITIONS,
        label="任务",
    )


def transition_order(
    current: OrderStatus | str,
    event: OrderEvent | str,
) -> OrderStatus:
    return _transition(
        current,
        event,
        status_type=OrderStatus,
        event_type=OrderEvent,
        transitions=_ORDER_TRANSITIONS,
        label="订单",
    )


def transition_aftersale(
    current: AftersaleStatus | str,
    event: AftersaleEvent | str,
) -> AftersaleStatus:
    return _transition(
        current,
        event,
        status_type=AftersaleStatus,
        event_type=AftersaleEvent,
        transitions=_AFTERSALE_TRANSITIONS,
        label="售后工单",
    )


def transition_approval(
    current: ApprovalStatus | str,
    event: ApprovalEvent | str,
) -> ApprovalStatus:
    return _transition(
        current,
        event,
        status_type=ApprovalStatus,
        event_type=ApprovalEvent,
        transitions=_APPROVAL_TRANSITIONS,
        label="审批",
    )


def _allowed_transitions(status_type, transitions, current) -> tuple:
    try:
        current_status = status_type(current)
    except ValueError as exc:
        raise InvalidStateTransition(f"未知状态：{current}") from exc
    return tuple(
        target
        for (source, _event), target in transitions.items()
        if source is current_status
    )


def allowed_task_transitions(
    current: TaskStatus | str,
) -> tuple[TaskStatus, ...]:
    return _allowed_transitions(TaskStatus, _TASK_TRANSITIONS, current)


def allowed_order_transitions(
    current: OrderStatus | str,
) -> tuple[OrderStatus, ...]:
    return _allowed_transitions(OrderStatus, _ORDER_TRANSITIONS, current)


def allowed_aftersale_transitions(
    current: AftersaleStatus | str,
) -> tuple[AftersaleStatus, ...]:
    return _allowed_transitions(
        AftersaleStatus,
        _AFTERSALE_TRANSITIONS,
        current,
    )


def allowed_approval_transitions(
    current: ApprovalStatus | str,
) -> tuple[ApprovalStatus, ...]:
    return _allowed_transitions(
        ApprovalStatus,
        _APPROVAL_TRANSITIONS,
        current,
    )
