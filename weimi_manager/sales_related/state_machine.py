from __future__ import annotations

from enum import StrEnum

from .constants import TERMINAL_TASK_STATUSES


class SalesTaskStatus(StrEnum):
    PENDING = "pending"
    PRODUCT_IDENTIFIED = "product_identified"
    SKU_CONFIRMED = "sku_confirmed"
    CHECKOUT_VERIFIED = "checkout_verified"
    AWAITING_USER_CONFIRMATION = "awaiting_user_confirmation"
    SUBMISSION_PENDING_VERIFICATION = "submission_pending_verification"
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CLOSED = "closed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class SalesEvent(StrEnum):
    IDENTIFY_PRODUCT = "identify_product"
    CONFIRM_SKU = "confirm_sku"
    VERIFY_CHECKOUT = "verify_checkout"
    REQUEST_USER_CONFIRMATION = "request_user_confirmation"
    REQUEST_SUBMISSION = "request_submission"
    SUBMISSION_CONFIRMED = "submission_confirmed"
    SUBMISSION_NOT_FOUND = "submission_not_found"
    SUBMISSION_UNKNOWN = "submission_unknown"
    RESUME_AFTER_VERIFICATION = "resume_after_verification"
    MARK_PAID = "mark_paid"
    MARK_SHIPPED = "mark_shipped"
    COMPLETE = "complete"
    CLOSE = "close"
    FAIL = "fail"


class InvalidTaskTransition(ValueError):
    pass


_TRANSITIONS: dict[
    tuple[SalesTaskStatus, SalesEvent],
    SalesTaskStatus,
] = {
    (SalesTaskStatus.PENDING, SalesEvent.IDENTIFY_PRODUCT):
        SalesTaskStatus.PRODUCT_IDENTIFIED,
    (SalesTaskStatus.PRODUCT_IDENTIFIED, SalesEvent.CONFIRM_SKU):
        SalesTaskStatus.SKU_CONFIRMED,
    (SalesTaskStatus.SKU_CONFIRMED, SalesEvent.VERIFY_CHECKOUT):
        SalesTaskStatus.CHECKOUT_VERIFIED,
    (SalesTaskStatus.CHECKOUT_VERIFIED, SalesEvent.REQUEST_USER_CONFIRMATION):
        SalesTaskStatus.AWAITING_USER_CONFIRMATION,
    (
        SalesTaskStatus.AWAITING_USER_CONFIRMATION,
        SalesEvent.REQUEST_SUBMISSION,
    ): SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION,
    (
        SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION,
        SalesEvent.SUBMISSION_CONFIRMED,
    ): SalesTaskStatus.PENDING_PAYMENT,
    (
        SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION,
        SalesEvent.SUBMISSION_NOT_FOUND,
    ): SalesTaskStatus.CHECKOUT_VERIFIED,
    (
        SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION,
        SalesEvent.SUBMISSION_UNKNOWN,
    ): SalesTaskStatus.UNKNOWN,
    (SalesTaskStatus.UNKNOWN, SalesEvent.RESUME_AFTER_VERIFICATION):
        SalesTaskStatus.SUBMISSION_PENDING_VERIFICATION,
    (SalesTaskStatus.PENDING_PAYMENT, SalesEvent.MARK_PAID):
        SalesTaskStatus.PAID,
    (SalesTaskStatus.PAID, SalesEvent.MARK_SHIPPED):
        SalesTaskStatus.SHIPPED,
    (SalesTaskStatus.SHIPPED, SalesEvent.COMPLETE):
        SalesTaskStatus.COMPLETED,
}

for _status in SalesTaskStatus:
    if _status.value in TERMINAL_TASK_STATUSES:
        continue
    _TRANSITIONS[(_status, SalesEvent.CLOSE)] = SalesTaskStatus.CLOSED
    _TRANSITIONS[(_status, SalesEvent.FAIL)] = SalesTaskStatus.FAILED


def transition_task(
    current: SalesTaskStatus | str,
    event: SalesEvent | str,
) -> SalesTaskStatus:
    try:
        current_status = SalesTaskStatus(current)
    except ValueError as exc:
        raise InvalidTaskTransition(f"未知任务状态：{current}") from exc
    try:
        task_event = SalesEvent(event)
    except ValueError as exc:
        raise InvalidTaskTransition(f"未知任务事件：{event}") from exc
    target = _TRANSITIONS.get((current_status, task_event))
    if target is None:
        raise InvalidTaskTransition(
            f"任务不能从“{current_status.value}”执行“{task_event.value}”"
        )
    return target
