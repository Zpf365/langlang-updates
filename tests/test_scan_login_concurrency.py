from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from weimi_manager.ui.app import MainWindow
from weimi_manager.ui.store_page import StorePage


class ScanLoginConcurrencyTests(unittest.TestCase):
    def test_auto_refresh_does_not_start_during_scan_login(self) -> None:
        window = object.__new__(MainWindow)
        window.auto_refresh_enabled = True
        window._auto_refresh_running = False
        window.has_running_task = mock.Mock(return_value=True)
        window.winfo_exists = lambda: True
        window.after = mock.Mock()

        window._auto_refresh_tick()

        window.has_running_task.assert_called_once_with(
            "stores:scan-login"
        )
        window.after.assert_called_once_with(60000, window._auto_refresh_tick)
        self.assertFalse(hasattr(window, "_auto_tick_count"))

    def test_scan_login_authorizes_only_the_first_pending_store(self) -> None:
        stores = [
            SimpleNamespace(
                id=1,
                name="店铺一",
                app_id="wx-store-one",
                web_status="未登录",
            ),
            SimpleNamespace(
                id=2,
                name="店铺二",
                app_id="wx-store-two",
                web_status="未登录",
            ),
        ]
        captured: dict[str, object] = {}

        class FakeWindow:
            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def run_task(
                _work,
                _done,
                _error,
                **kwargs,
            ) -> bool:
                captured.update(kwargs)
                return True

        page = object.__new__(StorePage)
        page.window = FakeWindow()
        page.db = SimpleNamespace(list_stores=lambda: stores)
        page._selected_stores = lambda: stores
        page._has_permission = lambda _permission: True

        page._scan_login()

        self.assertEqual(captured.get("store_app_id"), "wx-store-one")
        self.assertNotIn("store_app_ids", captured)

    def test_account_sync_does_not_start_during_scan_login(self) -> None:
        window = object.__new__(MainWindow)
        window.account_store_sync = SimpleNamespace(
            push=mock.Mock(),
        )
        window._account_sync_running = False
        window._account_sync_job = None
        window._task_running = True
        window._running_tasks = {
            "stores:scan-login": {object()},
        }
        window.has_running_task = mock.Mock(return_value=True)
        window.winfo_exists = lambda: False
        window.db = SimpleNamespace(
            start_task_log=mock.Mock(return_value=1),
        )

        with mock.patch(
            "weimi_manager.ui.app.threading.Thread"
        ) as thread:
            window._push_account_store_sync()

        thread.assert_not_called()
        window.account_store_sync.push.assert_not_called()
        self.assertFalse(window._account_sync_running)

    def test_auto_refresh_only_queries_logged_in_stores(self) -> None:
        logged = SimpleNamespace(
            id=1,
            name="已登录店铺",
            app_id="wx-logged",
            web_status="已登录",
        )
        offline = SimpleNamespace(
            id=2,
            name="未登录店铺",
            app_id="wx-offline",
            web_status="未登录",
        )
        probes: list[str] = []
        order_syncs: list[str] = []

        class FakeDb:
            @staticmethod
            def list_stores():
                return [logged, offline]

            @staticmethod
            def get_setting(_key, default=""):
                return default

            @staticmethod
            def start_task_log(**_kwargs):
                return 1

            @staticmethod
            def set_setting(_key, _value):
                return None

            @staticmethod
            def update_web_status(_store_id, _status, _detail):
                return None

        done = threading.Event()

        window = object.__new__(MainWindow)
        window.auto_refresh_enabled = True
        window._auto_refresh_running = False
        window._auto_refresh_batch_size = 50
        window._auto_refresh_workers = 2
        window._auto_tick_count = 0
        window._task_running = False
        window._account_sync_running = False
        window._active_page = "none"
        window.db = FakeDb()
        window.web_session = SimpleNamespace(
            probe=lambda store: (
                probes.append(store.app_id) or ("已登录", "ok")
            )
        )
        window.service = SimpleNamespace(
            sync_recent_order_updates=lambda store, *_args: (
                order_syncs.append(store.app_id)
            ),
            sync_after_sales=lambda store, *_args, **_kwargs: None,
            sync_analytics_day=lambda *_args, **_kwargs: None,
        )
        window.has_running_task = lambda *_args: False
        window.winfo_exists = lambda: False
        window.after = mock.Mock()
        window.set_status = mock.Mock()
        window._finish_task_log = mock.Mock()
        window._refresh_nav_alerts = mock.Mock()
        def post(callback, *args):
            callback(*args)
            done.set()

        window._post = post
        window._auto_refresh_tick()
        self.assertTrue(done.wait(2))

        self.assertEqual(probes, ["wx-logged"])
        self.assertEqual(order_syncs, ["wx-logged"])


if __name__ == "__main__":
    unittest.main()
