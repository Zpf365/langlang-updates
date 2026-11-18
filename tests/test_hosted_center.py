from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.ui.hosted_center import (
    HOSTED_ACTIONS,
    SUPPORTED_ACTIONS,
    UNSUPPORTED_ACTIONS,
    HostedCenterPage,
)
from weimi_manager.ui.theme import apply_theme


class HostedCenterTests(unittest.TestCase):
    def test_action_capability_sets_are_explicit(self) -> None:
        self.assertTrue(SUPPORTED_ACTIONS)
        self.assertTrue(UNSUPPORTED_ACTIONS)
        self.assertTrue(SUPPORTED_ACTIONS.isdisjoint(UNSUPPORTED_ACTIONS))
        self.assertNotIn("listing_quota", SUPPORTED_ACTIONS)
        self.assertNotIn("listing_quota", UNSUPPORTED_ACTIONS)

    def test_page_renders_configurable_actions_and_saved_state(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "hosted-center.db")
            store_id = db.add_store("托管测试店铺", "wx-hosted-center", "secret")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                window = FakeWindow(db)
                page = HostedCenterPage(root, window)
                page.refresh()
                root.update_idletasks()

                self.assertEqual(
                    page.tree["columns"],
                    (
                        "store",
                        *(key for key, _label in HOSTED_ACTIONS),
                        "config",
                        "execute",
                    ),
                )
                values = page.tree.item("0", "values")
                labels = dict(HOSTED_ACTIONS)
                delete_index = 1 + tuple(labels).index("delete_no_traffic")
                commission_index = 1 + tuple(labels).index("modify_commission")
                self.assertIn("未配置", values[delete_index])
                self.assertIn("未配置", values[commission_index])

                db.update_hosted_config(
                    store_id,
                    enabled=True,
                    delete_no_traffic=True,
                    no_traffic_days=7,
                )
                page.refresh()
                values = page.tree.item("0", "values")
                self.assertIn("待执行", values[delete_index])
                self.assertEqual(
                    str(page.execute_configured_button["state"]),
                    "normal",
                )
                self.assertIn("1 个已启用项目", page.state_var.get())
                page._position_row_buttons()
                self.assertEqual(len(page._row_buttons), 1)
                self.assertIn("尚未执行", page.result_var.get())
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
