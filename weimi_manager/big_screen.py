"""Minimal standalone host for the legacy full-screen dashboard."""

from __future__ import annotations

import argparse
import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable

from .db import Database
from .runtime_logging import log_event
from .service import SyncService


def _enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class DashboardSyncService(SyncService):
    """Connect the legacy refresh action to the existing real services."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._procurement_synced = False
        self._procurement_lock = threading.Lock()

    def refresh_store_metrics(self, store):
        errors: list[str] = []
        now = int(datetime.now().timestamp())
        try:
            self.refresh_orders_recent(
                store,
                now - 48 * 60 * 60,
                now,
            )
        except Exception as exc:
            errors.append(f"订单同步：{exc}")
        try:
            self.sync_after_sales(
                store,
                now - 24 * 60 * 60,
                now,
                time_field="create",
            )
        except Exception as exc:
            errors.append(f"售后同步：{exc}")
        with self._procurement_lock:
            if not self._procurement_synced:
                self._procurement_synced = True
                for account in self.db.list_procurement_accounts():
                    account_id = int(account.get("id") or 0)
                    if account_id <= 0:
                        continue
                    try:
                        self.sync_procurement_account(account_id)
                    except Exception as exc:
                        errors.append(f"采购账号 {account_id}：{exc}")
        metrics = super().refresh_store_metrics(store)
        metrics["_dashboard_sync_errors"] = errors
        if errors:
            log_event(
                "dashboard.big_screen.partial_refresh",
                store_id=store.id,
                store_name=store.name,
                errors=errors,
            )
            raise RuntimeError("；".join(errors)[:1000])
        return metrics


def _parent_is_running(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        import psutil

        return bool(psutil.Process(pid).is_running())
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    except Exception:
        return False


class BigScreenAdapter:
    """The small MainWindow-compatible surface used by the legacy screen."""

    def __init__(
        self,
        root: tk.Tk,
        database: Database,
        *,
        parent_pid: int = 0,
    ) -> None:
        self.root = root
        self.db = database
        self.service = DashboardSyncService(database)
        self.parent_pid = int(parent_pid)
        self._big_screen_window: Any = None
        self._task_running = False
        self._task_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._monitor_stop = threading.Event()
        self._parent_exited = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    def run_task(
        self,
        work: Callable[[Callable[[str, int, int], None]], Any],
        done: Callable[[Any], None] | None = None,
        failed: Callable[[str], None] | None = None,
        progress: Callable[[str, int, int], None] | None = None,
        **_kwargs: Any,
    ) -> bool:
        if self._task_running:
            return False
        self._task_running = True

        def report_progress(message: str, current: int, total: int) -> None:
            self._task_queue.put(
                ("progress", (str(message), int(current), int(total)))
            )

        def worker() -> None:
            try:
                result = work(report_progress)
            except Exception as exc:
                self._task_queue.put(("failed", str(exc)))
            else:
                self._task_queue.put(("done", result))

        def poll() -> None:
            try:
                kind, value = self._task_queue.get_nowait()
            except queue.Empty:
                self.root.after(50, poll)
                return
            try:
                if kind == "progress" and callable(progress):
                    progress(*value)
                elif kind == "done" and callable(done):
                    self._task_running = False
                    done(value)
                elif kind == "failed" and callable(failed):
                    self._task_running = False
                    failed(value)
                elif kind in {"done", "failed"}:
                    self._task_running = False
            except Exception as exc:
                self._task_running = False
                self.set_status(f"数据大屏回调失败：{exc}", busy=False)
            if kind == "progress" and self.root.winfo_exists():
                self.root.after(50, poll)

        threading.Thread(
            target=worker,
            name="langlang-big-screen-task",
            daemon=True,
        ).start()
        self.root.after(50, poll)
        return True

    def set_status(self, message: str, busy: bool = False) -> None:
        text = str(message or "").strip()
        if not text:
            return
        log_event(
            "big_screen.status",
            message=text,
            busy=bool(busy),
        )
        page = getattr(self._big_screen_window, "screen_page", None)
        variable = getattr(page, "updated_var", None)
        if variable is not None:
            try:
                variable.set(text)
            except tk.TclError:
                pass
        if not busy and any(
            marker in text
            for marker in ("数据大屏刷新失败", "数据大屏异常")
        ):
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "数据大屏",
                    text,
                    parent=self._big_screen_window
                    if self._big_screen_window is not None
                    else self.root,
                ),
            )

    def winfo_exists(self) -> int:
        return int(self.root.winfo_exists())

    def deiconify(self) -> None:
        return None

    def lift(self) -> None:
        return None

    def focus_force(self) -> None:
        return None

    def start_parent_monitor(self) -> None:
        if self.parent_pid <= 0:
            return

        def monitor() -> None:
            while not self._monitor_stop.wait(0.75):
                if not _parent_is_running(self.parent_pid):
                    self._parent_exited.set()
                    return

        self._monitor_thread = threading.Thread(
            target=monitor,
            name="langlang-big-screen-parent-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        self._monitor_stop.set()


def main(argv: list[str] | None = None) -> int:
    _enable_windows_dpi_awareness()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--data-dir", default="")
    arguments, _unknown = parser.parse_known_args(argv)
    data_dir = (
        Path(arguments.data_dir).expanduser().resolve()
        if arguments.data_dir
        else Path(
            os.environ.get("WEIMI_DATA_DIR")
            or Path.home() / "AppData" / "Local" / "LangLang" / "data"
        )
    )
    database = Database(data_dir / "weimi_manager.db")
    root = tk.Tk()
    root.withdraw()
    adapter = BigScreenAdapter(
        root,
        database,
        parent_pid=arguments.parent_pid,
    )

    def report_callback_exception(exc_type, exc, traceback) -> None:
        message = f"{exc_type.__name__}: {exc}"
        adapter.set_status(f"数据大屏异常：{message}", busy=False)

    root.report_callback_exception = report_callback_exception
    from .ui.skeletons import BigScreenWindow

    class AutoNetworkBigScreenWindow(BigScreenWindow):
        def _auto_refresh(self) -> None:
            self.refresh_after_id = None
            if not self.winfo_exists():
                return
            self.screen_page._sync()
            self._schedule_refresh()

    window = AutoNetworkBigScreenWindow(root, adapter)
    adapter._big_screen_window = window
    adapter.start_parent_monitor()

    def watch_close() -> None:
        if not root.winfo_exists():
            return
        if (
            adapter._parent_exited.is_set()
            or adapter._big_screen_window is None
        ):
            adapter.stop()
            root.destroy()
            return
        root.after(200, watch_close)

    root.after(200, watch_close)
    root.after(800, window.screen_page._sync)
    try:
        root.mainloop()
    except tk.TclError:
        return 1
    finally:
        adapter.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
