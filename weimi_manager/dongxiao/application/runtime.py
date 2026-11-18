from __future__ import annotations

from pathlib import Path

from ..domain.operations import ApiCallRecord
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


class DongxiaoRuntime:
    def __init__(
        self,
        database_path: Path,
        sync_service,
        *,
        max_concurrent_tasks: int = 2,
        max_tasks_per_store: int = 1,
    ) -> None:
        self.repository = DongxiaoRepository(Path(database_path))
        try:
            sync_service.call_observer = self.record_api_call
        except (AttributeError, TypeError):
            pass
        self.platform = OfficialDongxiaoPlatformAdapter(sync_service)
        self.task_service = DongxiaoTaskService(
            self.repository,
            platform_write_gate=self.platform_writes_enabled,
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
            platform_write_gate=self.platform_writes_enabled,
        )
        self.preflight_service = DongxiaoPreflightService(
            self.repository,
            runtime=self,
            sync_service=sync_service,
            database_path=Path(database_path),
            backup_dir=Path(database_path).parent / "backups",
        )
        self.started = False

    def platform_writes_enabled(self) -> bool:
        return self.repository.platform_writes_enabled()

    def set_platform_writes_enabled(
        self,
        enabled: bool,
        *,
        actor_id: str,
        permission_checker,
    ) -> bool:
        require_permission(
            permission_checker,
            "dongxiao_platform_write_switch",
        )
        return self.repository.set_platform_writes_enabled(
            enabled,
            actor_id=actor_id,
        )

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
