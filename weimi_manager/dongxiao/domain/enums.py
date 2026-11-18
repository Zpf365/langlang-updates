from __future__ import annotations

from enum import StrEnum


class ProductState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"


class DataSource(StrEnum):
    OFFICIAL_API = "official_api"
    WEB_BACKEND = "web_backend"
    LOCAL_CACHE = "local_cache"
    MOCK = "mock"
    UNKNOWN = "unknown"


class PresenceState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"


class ProductPoolStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ProductImportStatus(StrEnum):
    READY = "ready"
    PENDING_RESOLUTION = "pending_resolution"
    INVALID = "invalid"
    DUPLICATE = "duplicate"


class TaskStatus(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class TaskEvent(StrEnum):
    START_PREPARING = "start_preparing"
    START_RUNNING = "start_running"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    PARTIAL_FAILURE = "partial_failure"
    FAIL = "fail"
    CANCEL = "cancel"
    MARK_UNKNOWN = "mark_unknown"
    RECOVER_UNKNOWN = "recover_unknown"


class TaskItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class TaskAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class OrderStatus(StrEnum):
    UNKNOWN = "unknown"
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDING = "refunding"
    REFUNDED = "refunded"


class OrderEvent(StrEnum):
    MARK_PENDING_PAYMENT = "mark_pending_payment"
    MARK_PAID = "mark_paid"
    MARK_SHIPPED = "mark_shipped"
    MARK_COMPLETED = "mark_completed"
    MARK_CANCELLED = "mark_cancelled"
    START_REFUND = "start_refund"
    MARK_REFUNDED = "mark_refunded"
    MARK_UNKNOWN = "mark_unknown"


class OrderVerificationSource(StrEnum):
    PLATFORM_API = "platform_api"
    MOCK = "mock"
    CLIENT_REPORT = "client_report"
    UNKNOWN = "unknown"


class AftersaleStatus(StrEnum):
    UNKNOWN = "unknown"
    REQUESTED = "requested"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AftersaleEvent(StrEnum):
    MARK_REQUESTED = "mark_requested"
    START_REVIEW = "start_review"
    APPROVE = "approve"
    REJECT = "reject"
    START_PROCESSING = "start_processing"
    COMPLETE = "complete"
    FAIL = "fail"
    RETRY = "retry"
    CANCEL = "cancel"
    MARK_UNKNOWN = "mark_unknown"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalEvent(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EXPIRE = "expire"
    CANCEL = "cancel"


class ApprovalSubjectType(StrEnum):
    TASK = "task"
    AFTERSALE = "aftersale"


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"
    INFO = "info"
