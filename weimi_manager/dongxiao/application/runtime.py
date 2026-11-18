from __future__ import annotations

from pathlib import Path

from ..infrastructure.executors import (
    AftersaleDecisionExecutor,
    CompositeTaskExecutor,
)
from ..infrastructure.platform import OfficialDongxiaoPlatformAdapter
from ..infrastructure.repository import DongxiaoRepository
from .aftersale_service import DongxiaoAftersaleService
from .order_service import DongxiaoOrderService
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
        self.platform = OfficialDongxiaoPlatformAdapter(sync_service)
        self.order_service = DongxiaoOrderService(
            self.repository,
            self.platform,
        )
        self.aftersale_service = DongxiaoAftersaleService(
            self.repository,
            self.platform,
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
        self.task_service = DongxiaoTaskService(self.repository)
        self.scheduler = DongxiaoTaskScheduler(
            self.repository,
            self.executor,
            max_concurrent_tasks=max_concurrent_tasks,
            max_tasks_per_store=max_tasks_per_store,
        )
        self.started = False

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
