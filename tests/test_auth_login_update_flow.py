from __future__ import annotations

import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.ui.auth_window import AuthWindow
from weimi_manager.updater import StagedUpdate, UpdateInfo


def _interactive_descendants(widget: tk.Misc) -> list[tk.Misc]:
    interactive: list[tk.Misc] = []
    for child in widget.winfo_children():
        if isinstance(
            child,
            (
                tk.Button,
                tk.Checkbutton,
                tk.Entry,
                tk.Listbox,
                tk.Radiobutton,
                tk.Text,
            ),
        ):
            interactive.append(child)
        interactive.extend(_interactive_descendants(child))
    return interactive


class AuthLoginUpdateFlowTests(unittest.TestCase):
    def _window(self, root: Path) -> AuthWindow:
        service = SimpleNamespace(
            store=SimpleNamespace(
                list_accounts=lambda: [],
                find=lambda _username: None,
            )
        )
        paths = SimpleNamespace(
            resource_dir=root,
            project_dir=root,
        )
        try:
            return AuthWindow(paths, service)
        except tk.TclError as exc:
            self.skipTest(str(exc))

    def test_download_locks_all_login_interactions_and_blocks_close(self) -> None:
        info = UpdateInfo(
            current_version="1.8.79",
            latest_version="1.8.80",
            available=True,
            notes="测试更新",
            download_url="https://example.invalid/update.zip",
        )
        release_worker = threading.Event()
        worker_started = threading.Event()

        def blocked_download(*_args, **_kwargs):
            worker_started.set()
            release_worker.wait(timeout=5)
            raise RuntimeError("test worker released")

        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(Path(tmp))
            try:
                with mock.patch(
                    "weimi_manager.ui.auth_window.download_and_stage_update",
                    side_effect=blocked_download,
                ):
                    window._download_login_update(info)
                    self.assertTrue(worker_started.wait(timeout=2))
                    window.update_idletasks()

                    self.assertTrue(
                        getattr(window, "_update_in_progress", False)
                    )
                    states = [
                        str(widget.cget("state"))
                        for widget in _interactive_descendants(window)
                    ]
                    self.assertTrue(states)
                    self.assertTrue(
                        all(state == "disabled" for state in states),
                        states,
                    )
                    progress_dialog = getattr(
                        window,
                        "_login_update_dialog",
                        None,
                    )
                    self.assertIsNotNone(progress_dialog)
                    assert progress_dialog is not None
                    self.assertTrue(progress_dialog.winfo_exists())

                    with mock.patch(
                        "weimi_manager.ui.auth_window.messagebox.showwarning"
                    ) as warning:
                        window._close()
                        self.assertTrue(window.winfo_exists())
                        self.assertIn(
                            "正在更新",
                            str(warning.call_args),
                        )
            finally:
                release_worker.set()
                try:
                    window.destroy()
                except tk.TclError:
                    pass

    def test_automatic_update_check_only_shows_left_badge(self) -> None:
        info = UpdateInfo(
            current_version="1.8.80",
            latest_version="1.8.81",
            available=True,
            notes="测试更新",
            download_url="https://example.invalid/update.zip",
        )
        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(Path(tmp))
            try:
                with mock.patch(
                    "weimi_manager.ui.auth_window.messagebox.askyesno"
                ) as ask_yes_no:
                    window._update_checked(info, manual=False)
                    ask_yes_no.assert_not_called()
                    self.assertFalse(window._update_in_progress)
                    self.assertEqual(
                        str(window.login_button.cget("state")),
                        "normal",
                    )
                    self.assertEqual(
                        window.update_badge.winfo_manager(),
                        "place",
                    )
                    self.assertIn(
                        "1.8.81",
                        str(window.update_badge.cget("text")),
                    )
                    with mock.patch(
                        "weimi_manager.ui.auth_window.UpdateNotesDialog"
                    ) as details:
                        window._show_update_details()
                        details.assert_called_once()
                    first_details = window._update_details_dialog
                    if first_details is not None:
                        first_details.destroy()
                        window._clear_update_details()
                    window._update_checked(
                        UpdateInfo(
                            current_version="1.8.80",
                            latest_version="1.8.80",
                            available=False,
                        ),
                        manual=False,
                    )
                    self.assertEqual(
                        window.update_badge.winfo_manager(),
                        "",
                    )
            finally:
                try:
                    window.destroy()
                except tk.TclError:
                    pass

    def test_ready_does_not_show_second_yes_no_confirmation(self) -> None:
        info = UpdateInfo(
            current_version="1.8.79",
            latest_version="1.8.80",
            available=True,
            notes="测试更新",
            download_url="https://example.invalid/update.zip",
        )
        del info
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            window = self._window(root)
            staged = StagedUpdate(
                version="1.8.80",
                archive_path=root / "update.zip",
                staging_root=root / "stage",
                package_root=root / "stage" / "LangLang",
            )
            try:
                with (
                    mock.patch(
                        "weimi_manager.ui.auth_window.messagebox.askyesno"
                    ) as ask_yes_no,
                    mock.patch(
                        "weimi_manager.ui.auth_window.launch_update_installer",
                        return_value=root / "update.log",
                    ) as launch,
                ):
                    window._login_update_ready(staged)
                    ask_yes_no.assert_not_called()
                    launch.assert_called_once()
            finally:
                try:
                    window.destroy()
                except tk.TclError:
                    pass

    def test_progress_callbacks_are_queued_for_the_tk_thread(self) -> None:
        info = UpdateInfo(
            current_version="1.8.79",
            latest_version="1.8.80",
            available=True,
            download_url="https://example.invalid/update.zip",
        )
        release_worker = threading.Event()
        worker_started = threading.Event()
        thread_ids: list[int] = []

        def blocked_download(*_args, progress=None, **_kwargs):
            assert progress is not None
            thread_ids.append(threading.get_ident())
            progress("正在下载更新包", 512, 1024)
            worker_started.set()
            release_worker.wait(timeout=5)
            raise RuntimeError("released")

        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(Path(tmp))
            try:
                window._cancel_scheduled()
                with mock.patch(
                    "weimi_manager.ui.auth_window.download_and_stage_update",
                    side_effect=blocked_download,
                ):
                    window._download_login_update(info)
                    self.assertTrue(worker_started.wait(timeout=2))
                    panel = window._login_update_panel
                    self.assertIsNotNone(panel)
                    assert panel is not None
                    self.assertEqual(
                        panel.stage_var.get(),
                        "正在准备更新…",
                    )
                    self.assertNotEqual(
                        thread_ids[0],
                        threading.get_ident(),
                    )
                    window._drain_queue()
                    self.assertEqual(
                        panel.stage_var.get(),
                        "正在下载更新包",
                    )
                    self.assertIn("50%", panel.detail_var.get())
            finally:
                release_worker.set()
                try:
                    window.destroy()
                except tk.TclError:
                    pass

    def test_rapid_duplicate_update_click_starts_one_worker(self) -> None:
        info = UpdateInfo(
            current_version="1.8.79",
            latest_version="1.8.80",
            available=True,
            download_url="https://example.invalid/update.zip",
        )
        release_worker = threading.Event()
        worker_started = threading.Event()
        calls = 0

        def blocked_download(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            worker_started.set()
            release_worker.wait(timeout=5)
            raise RuntimeError("released")

        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(Path(tmp))
            try:
                with mock.patch(
                    "weimi_manager.ui.auth_window.download_and_stage_update",
                    side_effect=blocked_download,
                ):
                    window._download_login_update(info)
                    self.assertTrue(worker_started.wait(timeout=2))
                    window._download_login_update(info)
                    time.sleep(0.05)
                    self.assertEqual(calls, 1)
            finally:
                release_worker.set()
                try:
                    window.destroy()
                except tk.TclError:
                    pass

    def test_failure_restores_every_previously_enabled_control(self) -> None:
        info = UpdateInfo(
            current_version="1.8.79",
            latest_version="1.8.80",
            available=True,
            download_url="https://example.invalid/update.zip",
        )
        release_worker = threading.Event()
        worker_started = threading.Event()

        def blocked_download(*_args, **_kwargs):
            worker_started.set()
            release_worker.wait(timeout=5)
            raise RuntimeError("released")

        with tempfile.TemporaryDirectory() as tmp:
            window = self._window(Path(tmp))
            widgets = _interactive_descendants(window)
            initial_states = {
                widget: str(widget.cget("state"))
                for widget in widgets
            }
            try:
                with mock.patch(
                    "weimi_manager.ui.auth_window.download_and_stage_update",
                    side_effect=blocked_download,
                ):
                    window._download_login_update(info)
                    self.assertTrue(worker_started.wait(timeout=2))
                    with mock.patch(
                        "weimi_manager.ui.auth_window.messagebox.showerror"
                    ):
                        window._login_update_failed("磁盘空间不足")
                    self.assertFalse(window._update_in_progress)
                    self.assertFalse(window._update_worker_active)
                    self.assertEqual(
                        {
                            widget: str(widget.cget("state"))
                            for widget in widgets
                        },
                        initial_states,
                    )
                    self.assertIn(
                        "磁盘空间不足",
                        window.status_var.get(),
                    )
            finally:
                release_worker.set()
                try:
                    window.destroy()
                except tk.TclError:
                    pass
