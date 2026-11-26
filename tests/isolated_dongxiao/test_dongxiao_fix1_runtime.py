from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import weimi_manager.ui.app as app_module
from weimi_manager.ui.app import MainWindow
from weimi_manager.ui.dongxiao_page import DongxiaoPage


class _Diagnostics:
    def __init__(self) -> None:
        self.errors: list[BaseException] = []

    def capture_exception(self, error, **_kwargs):
        self.errors.append(error)


def test_runtime_initialization_failure_is_recorded_and_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class BrokenRuntime:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("runtime init failed")

    monkeypatch.setattr(app_module, "DongxiaoRuntime", BrokenRuntime)
    window = object.__new__(MainWindow)
    window.dongxiao_runtime = None
    window.dongxiao_runtime_error = ""
    window.base_db = SimpleNamespace(path=tmp_path / "runtime.db")
    window.service = object()
    window.diagnostics = _Diagnostics()

    assert window.ensure_dongxiao_runtime() is None
    assert "runtime init failed" in window.dongxiao_runtime_error
    assert len(window.diagnostics.errors) == 1

    class RecoveredRuntime:
        def __init__(self, *_args, **_kwargs):
            self.started = False

        def start_if_needed(self):
            return False

    monkeypatch.setattr(app_module, "DongxiaoRuntime", RecoveredRuntime)
    recovered = window.ensure_dongxiao_runtime()
    assert isinstance(recovered, RecoveredRuntime)
    assert window.dongxiao_runtime is recovered
    assert window.dongxiao_runtime_error == ""


def test_page_does_not_create_task_without_runtime(
    monkeypatch,
) -> None:
    errors: list[str] = []
    monkeypatch.setattr(
        "weimi_manager.ui.dongxiao_page.messagebox.showerror",
        lambda _title, message, **_kwargs: errors.append(str(message)),
    )
    page = object.__new__(DongxiaoPage)
    page.runtime = None
    page.task_scheduler = None
    page.window = SimpleNamespace(
        dongxiao_runtime_error="runtime unavailable",
        ensure_dongxiao_runtime=lambda: None,
    )
    page.source_status_var = SimpleNamespace(set=lambda _value: None)
    page._current_store = lambda: SimpleNamespace(id=1, app_id="wx-store")
    page._current_pool = lambda: SimpleNamespace(pool_id="pool-1")

    page._create_pool_task()

    assert errors
    assert "未创建任务" in errors[0]
    assert "runtime unavailable" in errors[0]
