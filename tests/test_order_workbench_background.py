from __future__ import annotations

import unittest
from unittest import mock

from weimi_manager.ui.order_workbench import MessageCenterPage, StoreAfterSalePage


class OrderWorkbenchBackgroundTests(unittest.TestCase):
    def test_batch_note_runs_in_background(self) -> None:
        calls = {
            "updates": [],
            "dialog": None,
        }

        class FakeDb:
            @staticmethod
            def get_order_workbench_row(store_id, order_id):
                return {
                    "store_id": store_id,
                    "order_id": order_id,
                    "note": "旧备注",
                }

            @staticmethod
            def update_order_workbench(store_id, order_id, **changes):
                calls["updates"].append(
                    (store_id, order_id, changes)
                )
                return changes

        class FakeWindow:
            @staticmethod
            def run_task(work, done, _error, **kwargs):
                calls["used_run_task"] = True
                calls["task_scope"] = kwargs.get("task_scope")
                done(work(lambda *_args: None))
                return True

            @staticmethod
            def set_status(message, **_kwargs):
                calls["status"] = message

        class FakeDialog:
            def __init__(self, _parent, _title, _fields, on_save):
                calls["dialog"] = on_save

        page = object.__new__(StoreAfterSalePage)
        page.db = FakeDb()
        page.window = FakeWindow()
        page.refresh = lambda: calls.setdefault("refreshed", True)
        page._selected_after_sales = lambda: [
            {"store_id": 7, "order_id": "ORDER-1"},
            {"store_id": 8, "order_id": "ORDER-2"},
        ]

        with mock.patch(
            "weimi_manager.ui.order_workbench.SimpleFieldsDialog",
            FakeDialog,
        ):
            page._batch_note()
            calls["dialog"]({"note": "新备注"})

        self.assertTrue(calls["used_run_task"])
        self.assertEqual(calls["task_scope"], "workbench:batch-note")
        self.assertTrue(calls["refreshed"])
        self.assertEqual(len(calls["updates"]), 2)
        self.assertEqual(
            calls["updates"][0][2],
            {"note": "旧备注\n新备注", "is_marked": 1},
        )

    def test_copy_pickup_codes_marks_sent_in_background(self) -> None:
        calls = {"updates": [], "clipboard": None}

        class Table:
            @staticmethod
            def selection():
                return ("row-1", "row-2")

        class FakeDb:
            @staticmethod
            def update_order_workbench(store_id, order_id, **changes):
                calls["updates"].append(
                    (store_id, order_id, changes)
                )

        class FakeWindow:
            @staticmethod
            def run_task(work, done, _error, **kwargs):
                calls["used_run_task"] = True
                calls["task_scope"] = kwargs.get("task_scope")
                done(work(lambda *_args: None))
                return True

            @staticmethod
            def set_status(message, **_kwargs):
                calls["status"] = message

        page = object.__new__(MessageCenterPage)
        page.db = FakeDb()
        page.window = FakeWindow()
        page.table = Table()
        page._rows = {
            "row-1": {
                "pickup_code": "A-001",
                "_links": [
                    {"store_id": 21, "order_id": "ORDER-A"},
                ],
            },
            "row-2": {
                "pickup_code": "A-002",
                "_links": [
                    {"store_id": 22, "order_id": "ORDER-B"},
                ],
            },
        }
        page.clipboard_clear = lambda: None
        page.clipboard_append = lambda value: calls.__setitem__(
            "clipboard",
            value,
        )
        page.refresh = lambda: calls.setdefault("refreshed", True)

        page._copy_codes()

        self.assertEqual(calls["clipboard"], "A-001\nA-002")
        self.assertTrue(calls["used_run_task"])
        self.assertEqual(calls["task_scope"], "workbench:mark-pickup-codes")
        self.assertTrue(calls["refreshed"])
        self.assertEqual(
            calls["updates"],
            [
                (21, "ORDER-A", {"is_marked_sent": 1}),
                (22, "ORDER-B", {"is_marked_sent": 1}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
