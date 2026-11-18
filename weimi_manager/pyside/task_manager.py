from __future__ import annotations

import logging
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

T = TypeVar("T")


class _WorkerSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)
    progress = Signal(str, str, int, int)


class _Worker(QRunnable):
    def __init__(
        self,
        task_id: str,
        function: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ):
        super().__init__()
        self.task_id = task_id
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.cancel_event = cancel_event
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            kwargs = dict(self.kwargs)
            if self.cancel_event is not None:
                kwargs["cancel_event"] = self.cancel_event
            result = self.function(*self.args, **kwargs)
            self.signals.completed.emit(self.task_id, result)
        except Exception as exc:  # noqa: BLE001 - 后台任务必须向 UI 返回可诊断异常。
            logging.getLogger("langlang").error(
                "后台任务失败：task_id=%s function=%s\n%s",
                self.task_id,
                getattr(self.function, "__name__", repr(self.function)),
                traceback.format_exc(),
            )
            self.signals.failed.emit(self.task_id, f"{type(exc).__name__}: {exc}")


@dataclass(slots=True)
class TaskHandle:
    task_id: str
    deduplication_key: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started: bool = False


class TaskManager(QObject):
    """全局后台任务管理器；任务生命周期不依赖页面对象。"""

    task_completed = Signal(str, object)
    task_failed = Signal(str, str)
    task_started = Signal(str)

    def __init__(self, max_workers: int = 8, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(16, int(max_workers))))
        self._lock = threading.RLock()
        self._handles: dict[str, TaskHandle] = {}
        self._keys: dict[str, str] = {}
        self._workers: dict[str, _Worker] = {}

    def submit(
        self,
        function: Callable[..., T],
        *args: Any,
        key: str | None = None,
        cancelable: bool = False,
        **kwargs: Any,
    ) -> str:
        """提交后台任务；相同 key 的任务运行中时拒绝重复提交。"""
        dedupe_key = key or f"task:{uuid.uuid4().hex}"
        with self._lock:
            current = self._keys.get(dedupe_key)
            if current and current in self._handles:
                raise RuntimeError("相同业务任务正在运行，请勿重复点击")
            task_id = uuid.uuid4().hex
            handle = TaskHandle(task_id, dedupe_key)
            worker = _Worker(
                task_id,
                function,
                args,
                kwargs,
                cancel_event=handle.cancel_event if cancelable else None,
            )
            worker.signals.completed.connect(self._on_completed)
            worker.signals.failed.connect(self._on_failed)
            self._handles[task_id] = handle
            self._keys[dedupe_key] = task_id
            self._workers[task_id] = worker
            self.task_started.emit(task_id)
            self._pool.start(worker)
            return task_id

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(task_id)
            if handle is None:
                return False
            handle.cancel_event.set()
            return True

    def cancel_all(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                handle.cancel_event.set()

    def active_task_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._handles)

    def _remove(self, task_id: str) -> None:
        handle = self._handles.pop(task_id, None)
        self._workers.pop(task_id, None)
        if handle and self._keys.get(handle.deduplication_key) == task_id:
            self._keys.pop(handle.deduplication_key, None)

    def _on_completed(self, task_id: str, result: object) -> None:
        with self._lock:
            self._remove(task_id)
        self.task_completed.emit(task_id, result)

    def _on_failed(self, task_id: str, message: str) -> None:
        with self._lock:
            self._remove(task_id)
        self.task_failed.emit(task_id, message)

    def wait_for_done(self, timeout_ms: int = 5000) -> bool:
        """等待后台任务结束；超时返回 False，不强制终止正在执行的网络请求。"""
        timeout = max(0, min(int(timeout_ms), 120000))
        return self._pool.waitForDone(timeout)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self.cancel_all()
        return self.wait_for_done(timeout_ms)
