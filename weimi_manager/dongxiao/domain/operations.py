from __future__ import annotations

from dataclasses import dataclass, field


class TaskExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.retryable = bool(retryable)
        self.unknown = bool(unknown)


@dataclass(frozen=True, slots=True)
class OperationSyncResult:
    discovered: int
    saved: int
    failed: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AftersaleAmountPreview:
    paid_amount_cents: int
    requested_amount_cents: int
    approved_amount_cents: int
    refunded_amount_cents: int
    inflight_amount_cents: int
    remaining_before_approval_cents: int
    remaining_after_approval_cents: int


@dataclass(frozen=True, slots=True)
class TaskStatistics:
    total: int = 0
    pending: int = 0
    running: int = 0
    paused: int = 0
    completed: int = 0
    failed: int = 0
    unknown: int = 0
    cancelled: int = 0
    execution_items: int = 0
    succeeded_items: int = 0
    failed_items: int = 0
    unknown_items: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
