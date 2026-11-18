from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..domain.enums import (
    TaskAttemptStatus,
    TaskItemStatus,
    TaskStatus,
)
from ..domain.models import (
    Task,
    TaskAttempt,
    TaskExecutionItem,
)
from ..domain.operations import TaskExecutionError
from ..infrastructure.repository import DongxiaoRepository


class TaskItemExecutor(Protocol):
    def execute(self, item: TaskExecutionItem) -> dict[str, object]: ...

    def verify(self, item: TaskExecutionItem) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0

    def delay_for(self, attempt_no: int) -> float:
        exponent = max(0, int(attempt_no) - 1)
        return min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2**exponent),
        )


@dataclass(slots=True)
class _ActiveTask:
    task_id: str
    store_id: int
    future: Future[None]


class DongxiaoTaskScheduler:
    def __init__(
        self,
        repository: DongxiaoRepository,
        executor: TaskItemExecutor,
        *,
        max_concurrent_tasks: int = 2,
        max_tasks_per_store: int = 1,
        retry_policy: RetryPolicy | None = None,
        worker_id: str = "",
        poll_interval_seconds: float = 0.2,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.max_concurrent_tasks = max(1, min(16, int(max_concurrent_tasks)))
        self.max_tasks_per_store = max(1, int(max_tasks_per_store))
        self.retry_policy = retry_policy or RetryPolicy()
        self.worker_id = str(worker_id or uuid.uuid4().hex)
        self.poll_interval_seconds = max(0.02, float(poll_interval_seconds))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_concurrent_tasks,
            thread_name_prefix="dongxiao-task",
        )
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._active: dict[str, _ActiveTask] = {}
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._dispatcher: threading.Thread | None = None

    def start(self) -> None:
        self.repository.recover_interrupted_tasks()
        with self._lock:
            if self._dispatcher and self._dispatcher.is_alive():
                return
            self._stopping.clear()
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                name="dongxiao-task-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._wake.set()
        with self._lock:
            active = list(self._active.values())
        for entry in active:
            entry.future.cancel()
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            while self._active and time.monotonic() < deadline:
                self._idle.wait(timeout=min(0.2, deadline - time.monotonic()))
        self._executor.shutdown(wait=False, cancel_futures=False)

    def enqueue(self, task_id: str = "") -> None:
        del task_id
        self._wake.set()

    def run_until_idle(self, *, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.1, float(timeout))
        while time.monotonic() < deadline:
            self._dispatch_due()
            with self._lock:
                active_count = len(self._active)
            if active_count:
                with self._idle:
                    self._idle.wait(timeout=min(0.05, deadline - time.monotonic()))
                continue
            if not self.repository.list_runnable_tasks(limit=1):
                return True
            time.sleep(min(0.01, self.poll_interval_seconds))
        return False

    def wait_for_idle(self, *, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._idle:
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(timeout=remaining)
        return not self.repository.list_runnable_tasks(limit=1)

    def verify_unknown_task(
        self,
        task_id: str,
        *,
        store_app_id: str,
        actor_id: str = "",
    ) -> tuple[Task, int]:
        task = self.repository.get_task(
            task_id,
            store_app_id=store_app_id,
        )
        if task is None:
            raise ValueError("任务不存在")
        if task.status is not TaskStatus.UNKNOWN:
            raise ValueError("只有结果未知任务需要远端核验")
        unknown_items = self.repository.list_task_execution_items(
            task_id,
            store_app_id=store_app_id,
            statuses=(TaskItemStatus.UNKNOWN,),
            limit=100000,
        )
        verified_count = 0
        unresolved = 0
        for item in unknown_items:
            try:
                verified = dict(self.executor.verify(item))
            except Exception:
                unresolved += 1
                continue
            self.repository.mark_execution_item_verified(
                item.item_id,
                store_app_id=store_app_id,
                result=verified,
            )
            verified_count += 1
        if unresolved:
            self.repository.update_task_progress(
                task_id,
                store_id=task.store_id,
                store_app_id=store_app_id,
                succeeded_items=task.succeeded_items,
                failed_items=task.failed_items,
                status_reason=f"仍有 {unresolved} 条远端结果无法确认",
                last_error="远端状态未变化，任务保持结果未知",
            )
            return task, verified_count
        items = self.repository.list_task_execution_items(
            task_id,
            store_app_id=store_app_id,
            limit=100000,
        )
        succeeded = sum(
            item.status is TaskItemStatus.SUCCEEDED for item in items
        )
        failed = sum(item.status is TaskItemStatus.FAILED for item in items)
        self.repository.update_task_progress(
            task_id,
            store_id=task.store_id,
            store_app_id=store_app_id,
            succeeded_items=succeeded,
            failed_items=failed,
            status_reason="远端结果已核验",
        )
        recovered = self.repository.transition_task(
            task_id,
            store_id=task.store_id,
            store_app_id=store_app_id,
            event="recover_unknown",
            actor_id=actor_id,
            reason="已通过官方接口核验 unknown 明细",
            remote_result_verified=True,
        )
        self.enqueue(task_id)
        return recovered, verified_count

    def _dispatch_loop(self) -> None:
        while not self._stopping.is_set():
            self._dispatch_due()
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()

    def _dispatch_due(self) -> None:
        with self._lock:
            active_task_ids = set(self._active)
            active_by_store: dict[int, int] = {}
            for entry in self._active.values():
                active_by_store[entry.store_id] = (
                    active_by_store.get(entry.store_id, 0) + 1
                )
        available = self.max_concurrent_tasks - len(active_task_ids)
        if available <= 0:
            return
        for task in self.repository.list_runnable_tasks(limit=100):
            if task.task_id in active_task_ids:
                continue
            if active_by_store.get(task.store_id, 0) >= self.max_tasks_per_store:
                continue
            future = self._executor.submit(self._execute_task, task)
            entry = _ActiveTask(
                task_id=task.task_id,
                store_id=task.store_id,
                future=future,
            )
            with self._lock:
                self._active[task.task_id] = entry
            active_task_ids.add(task.task_id)
            active_by_store[task.store_id] = (
                active_by_store.get(task.store_id, 0) + 1
            )
            future.add_done_callback(
                lambda completed, task_id=task.task_id: self._task_finished(
                    task_id,
                    completed,
                )
            )
            available -= 1
            if available <= 0:
                break

    def _task_finished(self, task_id: str, future: Future[None]) -> None:
        try:
            future.result()
        except Exception:
            pass
        with self._idle:
            self._active.pop(task_id, None)
            self._idle.notify_all()
        self._wake.set()

    def _execute_task(self, task: Task) -> None:
        try:
            self._execute_task_impl(task)
        except Exception as exc:
            current = self.repository.get_task(
                task.task_id,
                store_app_id=task.store_app_id,
            )
            if current is None:
                return
            self.repository.update_task_progress(
                current.task_id,
                store_id=current.store_id,
                store_app_id=current.store_app_id,
                succeeded_items=current.succeeded_items,
                failed_items=current.failed_items,
                status_reason="调度器内部异常",
                last_error=str(exc)[:1000],
            )
            if current.status in {
                TaskStatus.PREPARING,
                TaskStatus.RUNNING,
            }:
                self.repository.transition_task(
                    current.task_id,
                    store_id=current.store_id,
                    store_app_id=current.store_app_id,
                    event="fail",
                    actor_id="scheduler",
                    reason=f"调度器内部异常：{exc}",
                )

    def _execute_task_impl(self, task: Task) -> None:
        current = self.repository.get_task(
            task.task_id,
            store_app_id=task.store_app_id,
        )
        if current is None:
            return
        if current.status is TaskStatus.PENDING:
            current = self.repository.transition_task(
                current.task_id,
                store_id=current.store_id,
                store_app_id=current.store_app_id,
                event="start_preparing",
                actor_id="scheduler",
                reason="任务进入准备阶段",
            )
        if current.status is TaskStatus.PREPARING:
            current = self.repository.transition_task(
                current.task_id,
                store_id=current.store_id,
                store_app_id=current.store_app_id,
                event="start_running",
                actor_id="scheduler",
                reason="任务开始执行",
            )
        if current.status is not TaskStatus.RUNNING:
            return
        persisted_items = self.repository.list_task_execution_items(
            current.task_id,
            store_app_id=current.store_app_id,
            limit=100000,
        )
        if len(persisted_items) != current.total_items:
            self.repository.update_task_progress(
                current.task_id,
                store_id=current.store_id,
                store_app_id=current.store_app_id,
                succeeded_items=0,
                failed_items=0,
                status_reason="任务明细不完整，拒绝执行",
                last_error=(
                    f"预期 {current.total_items} 项，"
                    f"实际 {len(persisted_items)} 项"
                ),
            )
            self.repository.transition_task(
                current.task_id,
                store_id=current.store_id,
                store_app_id=current.store_app_id,
                event="fail",
                actor_id="scheduler",
                reason="任务明细持久化不完整",
            )
            return
        started_at = current.started_at or datetime.now(UTC)
        self.repository.update_task_progress(
            current.task_id,
            store_id=current.store_id,
            store_app_id=current.store_app_id,
            succeeded_items=current.succeeded_items,
            failed_items=current.failed_items,
            started_at=started_at,
        )
        batch_execute = getattr(self.executor, "execute_batch", None)
        while not self._stopping.is_set():
            latest = self.repository.get_task(
                current.task_id,
                store_app_id=current.store_app_id,
            )
            if latest is None or latest.status in {
                TaskStatus.PAUSED,
                TaskStatus.CANCELLED,
                TaskStatus.UNKNOWN,
            }:
                return
            if callable(batch_execute):
                batch = self.repository.claim_pending_execution_items(
                    current.task_id,
                    store_app_id=current.store_app_id,
                    owner=self.worker_id,
                    limit=100,
                )
                if not batch:
                    break
                batch_results = dict(batch_execute(batch) or {})
                if not batch_results:
                    for item in batch:
                        self._execute_item(item)
                    continue
                updates = []
                attempts = []
                finished = datetime.now(UTC)
                for item in batch:
                    result, error = batch_results.get(
                        item.item_id,
                        (
                            None,
                            TaskExecutionError(
                                "batch_missing_result",
                                "批处理没有返回该明细结果",
                            ),
                        ),
                    )
                    (
                        item_status,
                        attempt_status,
                        result_data,
                        next_attempt_at,
                    ) = self._classify_result(
                        item,
                        result,
                        error,
                        finished,
                    )
                    updates.append(
                        (
                            item.item_id,
                            item_status,
                            result_data,
                            error.code if error else "",
                            str(error) if error else "",
                            next_attempt_at,
                        )
                    )
                    attempts.append(
                        TaskAttempt(
                            attempt_id=uuid.uuid4().hex,
                            task_id=item.task_id,
                            item_id=item.item_id,
                            store_id=item.store_id,
                            store_app_id=item.store_app_id,
                            attempt_no=item.attempt_count,
                            status=attempt_status,
                            request_id=item.item_id,
                            started_at=item.started_at or finished,
                            finished_at=finished,
                            error_code=error.code if error else "",
                            error_message=str(error) if error else "",
                            result=result_data,
                        )
                    )
                self.repository.finish_task_execution_items_batch(
                    store_app_id=current.store_app_id,
                    updates=updates,
                    attempts=attempts,
                )
                continue
            item = self.repository.claim_next_execution_item(
                current.task_id,
                store_app_id=current.store_app_id,
                owner=self.worker_id,
            )
            if item is None:
                break
            self._execute_item(item)
        self._finalize_task(current)

    def _execute_item(self, item: TaskExecutionItem) -> None:
        started = item.started_at or datetime.now(UTC)
        result: dict[str, object] = {}
        error: TaskExecutionError | None = None
        try:
            result = dict(self.executor.execute(item) or {})
        except TaskExecutionError as exc:
            error = exc
        except Exception as exc:
            error = TaskExecutionError(
                "executor_exception",
                str(exc),
                retryable=False,
            )
        finished = datetime.now(UTC)
        (
            item_status,
            attempt_status,
            result,
            next_attempt_at,
        ) = self._classify_result(item, result, error, finished)
        status_saved = self.repository.finish_task_execution_item(
            item.item_id,
            store_app_id=item.store_app_id,
            status=item_status,
            result=result,
            error_code=error.code if error else "",
            error_message=str(error) if error else "",
            next_attempt_at=next_attempt_at,
        )
        if not status_saved:
            attempt_status = TaskAttemptStatus.CANCELLED
        self.repository.record_execution_attempt(
            TaskAttempt(
                attempt_id=uuid.uuid4().hex,
                task_id=item.task_id,
                item_id=item.item_id,
                store_id=item.store_id,
                store_app_id=item.store_app_id,
                attempt_no=item.attempt_count,
                status=attempt_status,
                request_id=item.item_id,
                started_at=started,
                finished_at=finished,
                error_code=error.code if error else "",
                error_message=str(error) if error else "",
                result=result,
            )
        )

    def _classify_result(
        self,
        item: TaskExecutionItem,
        result: dict[str, object] | None,
        error: TaskExecutionError | None,
        finished: datetime,
    ) -> tuple[
        TaskItemStatus,
        TaskAttemptStatus,
        dict[str, object],
        datetime | None,
    ]:
        result_data = dict(result or {})
        attempt_status = TaskAttemptStatus.SUCCEEDED
        next_attempt_at: datetime | None = None
        if error is None:
            item_status = TaskItemStatus.SUCCEEDED
        elif error.unknown:
            item_status = TaskItemStatus.UNKNOWN
            attempt_status = TaskAttemptStatus.UNKNOWN
        elif error.retryable and item.attempt_count < item.max_attempts:
            item_status = TaskItemStatus.PENDING
            attempt_status = TaskAttemptStatus.FAILED
            next_attempt_at = finished + timedelta(
                seconds=self.retry_policy.delay_for(item.attempt_count)
            )
        else:
            item_status = TaskItemStatus.FAILED
            attempt_status = TaskAttemptStatus.FAILED
        return item_status, attempt_status, result_data, next_attempt_at

    def _finalize_task(self, task: Task) -> None:
        current = self.repository.get_task(
            task.task_id,
            store_app_id=task.store_app_id,
        )
        if current is None or current.status not in {
            TaskStatus.PREPARING,
            TaskStatus.RUNNING,
        }:
            return
        items = self.repository.list_task_execution_items(
            task.task_id,
            store_app_id=task.store_app_id,
            limit=100000,
        )
        succeeded = sum(
            item.status is TaskItemStatus.SUCCEEDED for item in items
        )
        failed = sum(item.status is TaskItemStatus.FAILED for item in items)
        pending = [item for item in items if item.status is TaskItemStatus.PENDING]
        unknown = any(item.status is TaskItemStatus.UNKNOWN for item in items)
        next_attempt_at = min(
            (
                item.next_attempt_at
                for item in pending
                if item.next_attempt_at is not None
            ),
            default=None,
        )
        last_error = next(
            (
                item.last_error_message
                for item in reversed(items)
                if item.last_error_message
            ),
            "",
        )
        finished_at = None if pending else datetime.now(UTC)
        current = self.repository.update_task_progress(
            current.task_id,
            store_id=current.store_id,
            store_app_id=current.store_app_id,
            succeeded_items=succeeded,
            failed_items=failed,
            status_reason=(
                "等待重试"
                if pending
                else ("存在结果未知明细" if unknown else "")
            ),
            last_error=last_error,
            next_run_at=next_attempt_at,
            finished_at=finished_at,
        )
        if pending:
            return
        if unknown:
            self.repository.transition_task(
                current.task_id,
                store_id=current.store_id,
                store_app_id=current.store_app_id,
                event="mark_unknown",
                actor_id="scheduler",
                reason="存在远端结果未知明细，禁止盲目重试",
            )
            return
        if failed:
            event = "partial_failure" if succeeded else "fail"
        else:
            event = "complete"
        self.repository.transition_task(
            current.task_id,
            store_id=current.store_id,
            store_app_id=current.store_app_id,
            event=event,
            actor_id="scheduler",
            reason=f"成功 {succeeded}，失败 {failed}",
        )
