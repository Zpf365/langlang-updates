from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..domain.operations import (
    ApiCallRecord,
    PlatformReadonlyVerification,
    PreflightReport,
    PreflightState,
)
from ..domain.rules import require_permission
from ..infrastructure.executors import (
    AftersaleDecisionExecutor,
    CompositeTaskExecutor,
)
from ..infrastructure.platform import OfficialDongxiaoPlatformAdapter
from ..infrastructure.repository import DongxiaoRepository
from .aftersale_service import DongxiaoAftersaleService
from .order_service import DongxiaoOrderService
from .preflight_service import DongxiaoPreflightService
from .task_scheduler import DongxiaoTaskScheduler
from .task_service import DongxiaoTaskService

_PROCESS_SESSION_ID = uuid.uuid4().hex
_SESSION_RESET_DATABASES: set[str] = set()
_SESSION_RESET_LOCK = threading.Lock()


class DongxiaoRuntime:
    def __init__(
        self,
        database_path: Path,
        sync_service,
        *,
        max_concurrent_tasks: int = 2,
        max_tasks_per_store: int = 1,
        clock=None,
    ) -> None:
        self.database_path = Path(database_path)
        self.repository = DongxiaoRepository(self.database_path)
        self.session_id = _PROCESS_SESSION_ID
        self.clock = clock or (lambda: datetime.now(UTC))
        self._verification_lock = threading.RLock()
        self._platform_readonly_verification: (
            PlatformReadonlyVerification | None
        ) = None
        database_key = str(self.database_path.resolve()).casefold()
        with _SESSION_RESET_LOCK:
            if database_key not in _SESSION_RESET_DATABASES:
                self.repository.reset_platform_writes_for_session(
                    session_id=self.session_id,
                )
                _SESSION_RESET_DATABASES.add(database_key)
        try:
            sync_service.call_observer = self.record_api_call
        except (AttributeError, TypeError):
            pass
        self.platform = OfficialDongxiaoPlatformAdapter(sync_service)
        self.task_service = DongxiaoTaskService(
            self.repository,
            platform_write_gate=self.platform_write_gate,
        )
        self.order_service = DongxiaoOrderService(
            self.repository,
            self.platform,
        )
        self.aftersale_service = DongxiaoAftersaleService(
            self.repository,
            self.platform,
            task_service=self.task_service,
        )
        self.decision_executor = AftersaleDecisionExecutor(
            self.repository,
            self.platform,
            self.aftersale_service,
            platform_write_gate=self.platform_write_gate,
        )
        self.executor = CompositeTaskExecutor(
            self.repository,
            order_service=self.order_service,
            aftersale_service=self.aftersale_service,
            aftersale_decision_executor=self.decision_executor,
        )
        self.scheduler = DongxiaoTaskScheduler(
            self.repository,
            self.executor,
            max_concurrent_tasks=max_concurrent_tasks,
            max_tasks_per_store=max_tasks_per_store,
            platform_write_gate=self.platform_write_gate,
        )
        self.preflight_service = DongxiaoPreflightService(
            self.repository,
            runtime=self,
            sync_service=sync_service,
            database_path=self.database_path,
            backup_dir=self.database_path.parent / "backups",
            clock=self.clock,
        )
        self.started = False

    def platform_writes_enabled(self) -> bool:
        return self.repository.platform_writes_enabled()

    def platform_write_gate(self, store_app_id: str) -> bool:
        normalized_store = str(store_app_id).strip()
        if not normalized_store or not self.platform_writes_enabled():
            return False
        with self._verification_lock:
            verification = self._platform_readonly_verification
        return bool(
            verification is not None
            and verification.is_valid(
                session_id=self.session_id,
                store_app_id=normalized_store,
                now=self.clock(),
            )
        )

    def remember_platform_readonly_report(
        self,
        report: PreflightReport,
        *,
        valid_for_seconds: int = 600,
    ) -> PlatformReadonlyVerification | None:
        store_app_id = str(report.store_app_id or "").strip()
        required_codes = {
            "store_authorization",
            "product_api",
            "order_api",
            "aftersale_read_api",
        }
        checks = {item.code: item for item in report.checks}
        platform_ready = (
            bool(store_app_id)
            and report.release_ready
            and required_codes.issubset(checks)
            and all(
                checks[code].state is PreflightState.PASSED
                for code in required_codes
            )
        )
        if not platform_ready:
            self.invalidate_platform_readonly_verification()
            return None
        digest_input = "\0".join(
            (
                self.session_id,
                store_app_id,
                report.generated_at.isoformat(),
                *(
                    f"{item.code}:{item.state.value}:{item.message}"
                    for item in report.checks
                ),
            )
        )
        verified_at = report.generated_at
        verification = PlatformReadonlyVerification(
            session_id=self.session_id,
            store_app_id=store_app_id,
            verified_at=verified_at,
            expires_at=verified_at
            + timedelta(seconds=max(60, int(valid_for_seconds))),
            report_fingerprint=hashlib.sha256(
                digest_input.encode("utf-8")
            ).hexdigest(),
        )
        with self._verification_lock:
            self._platform_readonly_verification = verification
        return verification

    def invalidate_platform_readonly_verification(self) -> None:
        with self._verification_lock:
            self._platform_readonly_verification = None

    def platform_readonly_verification(
        self,
        store_app_id: str,
    ) -> PlatformReadonlyVerification | None:
        with self._verification_lock:
            verification = self._platform_readonly_verification
        if verification is None:
            return None
        if not verification.is_valid(
            session_id=self.session_id,
            store_app_id=str(store_app_id),
            now=self.clock(),
        ):
            return None
        return verification

    def set_platform_writes_enabled(
        self,
        enabled: bool,
        *,
        store=None,
        actor_id: str,
        permission_checker,
    ) -> bool:
        require_permission(
            permission_checker,
            "dongxiao_platform_write_switch",
        )
        if enabled:
            if store is None:
                raise ValueError("开启平台写操作前必须选择已验证店铺")
            verification = self.platform_readonly_verification(
                str(store.app_id)
            )
            if verification is None:
                raise ValueError(
                    "当前店铺没有有效的真实只读验收结果，"
                    "请重新完成官方 Token、商品、订单和售后只读检查"
                )
        else:
            self.invalidate_platform_readonly_verification()
        return self.repository.set_platform_writes_enabled(
            enabled,
            actor_id=actor_id,
        )

    def scheduler_health(self) -> dict[str, object]:
        scheduler = self.scheduler
        healthy_thread = bool(scheduler.is_healthy())
        active = int(scheduler.active_task_count())
        runnable = bool(
            self.repository.list_runnable_tasks(limit=1)
        )
        should_run = self.started or active > 0 or runnable
        return {
            "healthy": healthy_thread if should_run else True,
            "idle": not should_run,
            "thread_alive": healthy_thread,
            "active_tasks": active,
            "runnable_tasks": runnable,
            "started": self.started,
        }

    def record_api_call(self, payload: dict[str, object]) -> None:
        self.repository.record_api_call(
            ApiCallRecord(
                call_id=str(payload["call_id"]),
                store_app_id=str(payload["store_app_id"]),
                request_id=str(payload["request_id"]),
                method=str(payload["method"]),
                path=str(payload["path"]),
                attempt_no=int(payload["attempt_no"]),
                outcome=str(payload["outcome"]),
                started_at=payload["started_at"],
                finished_at=payload["finished_at"],
                duration_ms=int(payload["duration_ms"]),
                errcode=(
                    int(payload["errcode"])
                    if payload.get("errcode") is not None
                    else None
                ),
                rid=str(payload.get("rid") or ""),
                error_type=str(payload.get("error_type") or ""),
                error_message=str(payload.get("error_message") or ""),
            )
        )

    def start(self) -> None:
        if self.started:
            return
        self.scheduler.start()
        self.started = True

    def start_if_needed(self) -> bool:
        if self.started:
            return True
        recovered = self.repository.recover_interrupted_tasks()
        if recovered == 0 and not self.repository.list_runnable_tasks(limit=1):
            return False
        self.start()
        return True

    def stop(self) -> None:
        if not self.started:
            return
        self.scheduler.stop()
        self.started = False
