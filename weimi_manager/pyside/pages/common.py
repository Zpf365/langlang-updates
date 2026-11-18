from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QMessageBox, QPushButton

from ..widgets import PageBase


class TaskPage(PageBase):
    """Cached page that submits work through the shared background manager."""

    def __init__(self, workspace, title: str, description: str, stale_seconds: int = 20) -> None:
        super().__init__(title, description, stale_seconds=stale_seconds)
        self.workspace = workspace
        self._task_callbacks: dict[str, tuple[Callable[[Any], None], QPushButton | None]] = {}
        manager = workspace.task_manager
        manager.task_completed.connect(self._on_task_completed)
        manager.task_failed.connect(self._on_task_failed)

    def submit(
        self,
        key: str,
        function: Callable[..., Any],
        on_success: Callable[[Any], None],
        button: QPushButton | None = None,
        *,
        cancelable: bool = False,
    ) -> None:
        if button is not None:
            button.setEnabled(False)
        try:
            task_id = self.workspace.task_manager.submit(
                function,
                key=key,
                cancelable=cancelable,
            )
            self._task_callbacks[task_id] = (on_success, button)
        except Exception as exc:
            if button is not None:
                button.setEnabled(True)
            QMessageBox.information(self, "任务未启动", str(exc))

    def _on_task_completed(self, task_id: str, result: object) -> None:
        callback = self._task_callbacks.pop(task_id, None)
        if callback is None:
            return
        success, button = callback
        if button is not None:
            button.setEnabled(True)
        success(result)

    def _on_task_failed(self, task_id: str, message: str) -> None:
        callback = self._task_callbacks.pop(task_id, None)
        if callback is None:
            return
        _success, button = callback
        if button is not None:
            button.setEnabled(True)
        QMessageBox.warning(self, "操作失败", message)
