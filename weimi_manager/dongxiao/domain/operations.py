from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


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


class PreflightState(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_VERIFIED = "not_verified"
    MANUAL_AUTHORIZATION_REQUIRED = "manual_authorization_required"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    code: str
    label: str
    state: PreflightState
    message: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PreflightReport:
    generated_at: datetime
    checks: tuple[PreflightCheck, ...]
    store_app_id: str = ""

    @property
    def counts(self) -> dict[str, int]:
        return {
            state.value: sum(
                item.state is state for item in self.checks
            )
            for state in PreflightState
        }

    @property
    def release_ready(self) -> bool:
        return all(
            item.state is PreflightState.PASSED
            for item in self.checks
        )


@dataclass(frozen=True, slots=True)
class ApiCallRecord:
    call_id: str
    store_app_id: str
    request_id: str
    method: str
    path: str
    attempt_no: int
    outcome: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    errcode: int | None = None
    rid: str = ""
    error_type: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class PlatformReadonlyVerification:
    session_id: str
    store_app_id: str
    verified_at: datetime
    expires_at: datetime
    report_fingerprint: str

    def is_valid(
        self,
        *,
        session_id: str,
        store_app_id: str,
        now: datetime,
    ) -> bool:
        return (
            self.session_id == session_id
            and self.store_app_id == store_app_id
            and now < self.expires_at
        )


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
