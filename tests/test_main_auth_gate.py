from __future__ import annotations

import os
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager import main as main_module
from weimi_manager.auth_config import AuthConfig
from weimi_manager.single_instance import SingleInstanceGuard


class MainAuthGateTests(unittest.TestCase):
    def test_single_instance_guard_rejects_second_same_directory(self) -> None:
        first = SingleInstanceGuard(Path("D:/GPT/test-single-instance"))
        second = SingleInstanceGuard(Path("D:/GPT/test-single-instance"))
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            first.close()
            second.close()

    def test_unconfigured_release_blocks_business_window(self) -> None:
        paths = mock.Mock()
        paths.data_dir = Path("D:/GPT/test-auth-blocked")
        guard = mock.Mock()
        guard.acquire.return_value = True
        with (
            mock.patch.object(
                main_module.AppPaths,
                "discover",
                return_value=paths,
            ),
            mock.patch.object(
                main_module.AuthConfig,
                "load",
                return_value=AuthConfig("", ""),
            ),
            mock.patch.object(main_module, "AuthWindow") as auth_window,
            mock.patch.object(main_module, "MainWindow") as main_window,
            mock.patch.object(
                main_module.sys,
                "frozen",
                True,
                create=True,
            ),
            mock.patch.object(
                main_module,
                "SingleInstanceGuard",
                return_value=guard,
            ),
            mock.patch.object(main_module, "show_fatal_error") as fatal_error,
        ):
            main_window.return_value.mainloop.return_value = None
            self.assertEqual(main_module.main(), 1)
        auth_window.assert_not_called()
        main_window.assert_not_called()
        fatal_error.assert_called_once()
        guard.close.assert_called_once()

    def test_source_build_without_demo_flag_blocks_business_window(self) -> None:
        paths = mock.Mock()
        paths.data_dir = Path("D:/GPT/test-auth-source-blocked")
        guard = mock.Mock()
        guard.acquire.return_value = True
        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch.object(
                main_module.AppPaths,
                "discover",
                return_value=paths,
            ),
            mock.patch.object(
                main_module.AuthConfig,
                "load",
                return_value=AuthConfig("", ""),
            ),
            mock.patch.object(
                main_module.sys,
                "frozen",
                False,
                create=True,
            ),
            mock.patch.object(
                main_module,
                "SingleInstanceGuard",
                return_value=guard,
            ),
            mock.patch.object(main_module, "show_fatal_error"),
            mock.patch.object(main_module, "MainWindow") as main_window,
        ):
            os.environ.pop("LANGLANG_ALLOW_UNCONFIGURED_DEMO", None)
            self.assertEqual(main_module.main(), 1)
        main_window.assert_not_called()

    def test_source_demo_requires_explicit_flag(self) -> None:
        paths = mock.Mock()
        guard = mock.Mock()
        guard.acquire.return_value = True
        with (
            mock.patch.dict(
                os.environ,
                {"LANGLANG_ALLOW_UNCONFIGURED_DEMO": "1"},
                clear=False,
            ),
            mock.patch.object(
                main_module.AppPaths,
                "discover",
                return_value=paths,
            ),
            mock.patch.object(
                main_module.AuthConfig,
                "load",
                return_value=AuthConfig("", ""),
            ),
            mock.patch.object(
                main_module.sys,
                "frozen",
                False,
                create=True,
            ),
            mock.patch.object(
                main_module,
                "SingleInstanceGuard",
                return_value=guard,
            ),
            mock.patch.object(main_module, "MainWindow") as main_window,
            mock.patch.object(main_module, "_write_store_startup_log"),
            mock.patch.object(main_module, "_force_store_page_startup"),
        ):
            main_window.return_value.mainloop.return_value = None
            self.assertEqual(main_module.main(), 0)
        main_window.assert_called_once_with(paths, auth_context=None)

    def test_cancelled_auth_never_opens_business_window(self) -> None:
        paths = mock.Mock()
        paths.data_dir = Path("D:/GPT/test-auth-data")
        auth_window = mock.Mock()
        auth_window.result = None
        guard = mock.Mock()
        guard.acquire.return_value = True
        with (
            mock.patch.object(
                main_module.AppPaths,
                "discover",
                return_value=paths,
            ),
            mock.patch.object(
                main_module.AuthConfig,
                "load",
                return_value=AuthConfig(
                    "https://demo.supabase.co",
                    "sb_publishable_demo",
                ),
            ),
            mock.patch.object(
                main_module,
                "build_auth_service",
                return_value=SimpleNamespace(),
            ),
            mock.patch.object(
                main_module,
                "AuthWindow",
                return_value=auth_window,
            ),
            mock.patch.object(main_module, "MainWindow") as main_window,
            mock.patch.object(
                main_module,
                "SingleInstanceGuard",
                return_value=guard,
            ),
        ):
            self.assertEqual(main_module.main(), 0)
        main_window.assert_not_called()

    def test_switch_account_returns_to_login_and_uses_separate_paths(self) -> None:
        base = mock.Mock(data_dir=Path("D:/GPT/test-auth-data/data"))
        first_paths, second_paths = mock.Mock(), mock.Mock()
        base.for_account.side_effect = [first_paths, second_paths]
        first_account = SimpleNamespace(account_id="first")
        second_account = SimpleNamespace(account_id="second")
        service = SimpleNamespace(client=None, store=mock.Mock())
        first_window = mock.Mock(return_to_login=True)
        second_window = mock.Mock(return_to_login=False)
        guard = mock.Mock()
        guard.acquire.return_value = True
        with (
            mock.patch.object(main_module.AppPaths, "discover", return_value=base),
            mock.patch.object(
                main_module.AuthConfig, "load",
                return_value=AuthConfig("https://demo.supabase.co", "sb_publishable_demo"),
            ),
            mock.patch.object(main_module, "SingleInstanceGuard", return_value=guard),
            mock.patch.object(main_module, "build_auth_service", return_value=service),
            mock.patch.object(
                main_module, "AuthWindow",
                side_effect=[mock.Mock(result=first_account), mock.Mock(result=second_account)],
            ) as login,
            mock.patch.object(
                main_module, "MainWindow", side_effect=[first_window, second_window],
            ) as main_window,
            mock.patch.object(main_module, "_write_store_startup_log"),
            mock.patch.object(main_module, "_force_store_page_startup"),
        ):
            self.assertEqual(main_module.main(), 0)
        self.assertEqual(login.call_args_list, [mock.call(base, service)] * 2)
        self.assertEqual(base.for_account.call_args_list, [mock.call("first"), mock.call("second")])
        self.assertEqual(main_window.call_args_list, [
            mock.call(first_paths, auth_context=first_account),
            mock.call(second_paths, auth_context=second_account),
        ])
        self.assertIs(first_window.saved_account_store, service.store)
        guard.close.assert_called_once_with()

    def test_account_menu_only_forgets_saved_login_when_logging_out(self) -> None:
        for forget in (False, True):
            with self.subTest(forget=forget):
                window = mock.Mock(
                    _closing=False,
                    auth_context=SimpleNamespace(account_id="first"),
                    _task_running=False,
                    _pages={},
                    return_to_login=False,
                )
                window.tk.splitlist.return_value = ("after#0",)
                main_module.MainWindow._return_to_login(window, forget_account=forget)
                self.assertIs(window.return_to_login, True)
                window.tk.call.assert_has_calls([
                    mock.call("after", "info"),
                    mock.call("after", "cancel", "after#0"),
                ])
                window._on_close.assert_called_once_with()
                if forget:
                    window.saved_account_store.remove.assert_called_once_with("first")
                else:
                    window.saved_account_store.remove.assert_not_called()

    def test_account_switch_closes_widgets_with_pending_callbacks(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        child = tk.Frame(root)
        child.after(1000, lambda: None)
        window = SimpleNamespace(
            _closing=False,
            auth_context=SimpleNamespace(account_id="first"),
            _task_running=False,
            _pages={},
            return_to_login=False,
            tk=root.tk,
            _on_close=root.destroy,
        )
        main_module.MainWindow._return_to_login(window)
        self.assertTrue(window.return_to_login)

    def test_account_switch_can_continue_after_task_confirmation(self) -> None:
        window = mock.Mock(
            _closing=False,
            auth_context=SimpleNamespace(account_id="first"),
            _task_running=True,
            _pages={},
            return_to_login=False,
        )
        window.tk.splitlist.return_value = ()
        with mock.patch.object(
            main_module.MainWindow._return_to_login.__globals__["messagebox"],
            "askyesno",
            return_value=True,
        ) as ask:
            main_module.MainWindow._return_to_login(window)
        ask.assert_called_once()
        self.assertTrue(window.return_to_login)
        window._on_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
