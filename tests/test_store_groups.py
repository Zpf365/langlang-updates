from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from weimi_manager.db import Database
from weimi_manager.ui.business_tools import StoreGroupsPage
from weimi_manager.ui.theme import apply_theme


class StoreGroupsPageTests(unittest.TestCase):
    def test_create_group_keeps_empty_group_available_for_assignment(
        self,
    ) -> None:
        class FakeWindow:
            def __init__(self, db: Database) -> None:
                self.db = db
                self.statuses: list[str] = []
                self.sync_requests = 0

            def set_status(self, message: str, **_kwargs) -> None:
                self.statuses.append(message)

            def store_data_changed(self) -> None:
                self.sync_requests += 1

        with tempfile.TemporaryDirectory() as tmp:
            root = tk.Tk()
            root.withdraw()
            apply_theme(root)
            try:
                db = Database(Path(tmp) / "groups.db")
                window = FakeWindow(db)
                page = StoreGroupsPage(root, window)
                page.group_name_var.set("新建空分组")
                page._create_group()
                page.refresh()

                self.assertEqual(db.list_store_groups(), ["新建空分组"])
                self.assertIn(
                    "新建空分组",
                    page.group_combo["values"],
                )
                self.assertEqual(window.sync_requests, 1)
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
