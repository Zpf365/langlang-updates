import json
import queue
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from weimi_manager.db import Database
from weimi_manager.service import SyncService
from weimi_manager.shop_private import ShopPrivateClient, ShopPrivateError
from weimi_manager.ui.pages import ProductsPage
from weimi_manager.ui.theme import apply_theme


class ProductFreshnessTests(unittest.TestCase):
    def test_failed_delete_keeps_cached_product_and_success_removes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            store_id = db.add_store("Fixture", "wx-fixture", "secret")
            store = db.get_store(store_id)
            product = ShopPrivateClient._parse_product(
                {"spuId": "one", "title": "One", "status": 5}
            )
            product.store_id = store_id
            db.upsert_product(product)
            client = SimpleNamespace(
                delete_product=lambda _id: {"errcode": 1, "errmsg": "rejected"}
            )
            service = SyncService(db)
            service.platform_write_authorizer = (
                lambda _app_id, _action: None
            )
            with self.assertRaises(RuntimeError):
                service.product_write(
                    store,
                    "delete",
                    product_id="one",
                    client=client,
                )
            self.assertIsNotNone(db.get_product(store_id, "one"))
            client.delete_product = lambda _id: {"errcode": 0}
            service.product_write(
                store,
                "delete",
                product_id="one",
                client=client,
            )
            self.assertIsNone(db.get_product(store_id, "one"))

    def test_snapshot_failure_rolls_back_without_clearing_previous_products(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            store_id = db.add_store("Fixture", "wx-fixture", "")
            old = ShopPrivateClient._parse_product({"spuId": "old", "title": "Old", "status": 5})
            old.store_id = store_id
            db.upsert_product(old)
            invalid = ShopPrivateClient._parse_product(
                {"spuId": "new", "title": "New", "status": 5}
            )
            invalid.store_id = store_id
            invalid.title = None
            with self.assertRaises(sqlite3.IntegrityError):
                db.replace_product_tab(store_id, "sales", [invalid])
            self.assertEqual([row["product_id"] for row in db.list_products()], ["old"])

    def test_real_product_page_hides_failed_shop_cache(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        apply_theme(root)
        try:
            with tempfile.TemporaryDirectory() as directory:
                db = Database(Path(directory) / "test.db")
                store_ids = [db.add_store(f"Shop {i}", f"wx-{i}", "") for i in range(3)]
                for store_id in store_ids:
                    old = ShopPrivateClient._parse_product(
                        {"spuId": "stale", "title": "Old cached item", "status": 5}
                    )
                    old.store_id = store_id
                    db.upsert_product(old)
                messages = queue.Queue()

                class Window:
                    _task_running = False

                    def __init__(self):
                        self.db = db
                        self.service = SyncService(db)

                    def set_status(self, *_args, **_kwargs):
                        pass

                    def _post(self, callback, *args):
                        messages.put((callback, args))

                    def run_task(self, work, done, failed, progress=None, **_kwargs):
                        self._task_running = True
                        def finish():
                            try:
                                result = work(progress or (lambda *args: None))
                            except Exception as exc:
                                self._task_running = False
                                failed(str(exc))
                            else:
                                self._task_running = False
                                done(result)
                        root.after(0, finish)
                        return True

                class Client(ShopPrivateClient):
                    def __init__(self, _db, store):
                        self.store = store

                    def scan_page(self, *_args, **_kwargs):
                        if self.store.id == store_ids[-1]:
                            raise ShopPrivateError("expired session")
                        return {"totalNum": 1, "productList": [
                            {"spuId": "fresh", "title": "Fresh remote item", "status": 5}
                        ]}

                window = Window()
                page = ProductsPage(root, window)
                page.pack()
                with (
                    patch("weimi_manager.service.ShopPrivateClient", Client),
                    patch.object(db, "get_web_cookies", return_value={"cookies": []}),
                    patch("weimi_manager.ui.pages.messagebox.showwarning") as warning,
                ):
                    page.refresh()
                    page._query_products()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        root.update()
                        while not messages.empty():
                            callback, args = messages.get_nowait()
                            callback(*args)
                        if not window._task_running and not page._query_loading:
                            break
                        time.sleep(0.01)
                    self.assertEqual(len(page.tree.get_children()), 2)
                    self.assertEqual({row["product_id"] for row in page._rows.values()}, {"fresh"})
                    self.assertEqual(page._fresh_store_ids, set(store_ids[:2]))
                    warning.assert_called_once()
                    self.assertIsNotNone(db.get_product(store_ids[-1], "stale"))
        finally:
            root.destroy()

    def test_query_replaces_deleted_and_changed_remote_products(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            store_id = db.add_store("Fixture", "wx-fixture", "")
            db.update_web_status(store_id, "已登录")
            remote = [{"spuId": "old", "title": "Old", "status": 5}]

            class Client(ShopPrivateClient):
                def __init__(self, _db, store):
                    self.store = store

                def scan_page(self, tab_key, page=1, page_size=50):
                    return {"totalNum": len(remote), "productList": list(remote)}

            page = object.__new__(ProductsPage)
            page.db = db
            page.window = SimpleNamespace(service=SyncService(db))
            page.cancel_event = threading.Event()
            with (
                patch("weimi_manager.service.ShopPrivateClient", Client),
                patch.object(db, "get_web_cookies", return_value={"cookies": []}),
            ):
                page._refresh_product_tabs("sales", None, lambda *args: None)
                remote[:] = [{"spuId": "new", "title": "New", "status": 5}]
                page._refresh_product_tabs("sales", None, lambda *args: None)
                self.assertEqual([row["product_id"] for row in db.list_products()], ["new"])
                remote.clear()
                page._refresh_product_tabs("sales", None, lambda *args: None)
                self.assertEqual(db.list_products(), [])

    def test_merchant_statuses_appear_in_their_tabs(self):
        page = object.__new__(ProductsPage)
        for tab, status in (
            ("sales", 5),
            ("audit", 2),
            ("failed", 3),
            ("draft", 0),
            ("strategy", 7),
        ):
            with self.subTest(tab=tab):
                product = ShopPrivateClient._parse_product(
                    {"spuId": "fixture", "title": "Fixture", "status": status}
                )
                row = asdict(product)
                row["raw_json"] = json.dumps(product.raw)
                self.assertTrue(page._matches_tab(row, tab))

    def test_incomplete_remote_scan_is_not_a_complete_snapshot(self):
        client = object.__new__(ShopPrivateClient)
        client.scan_page = lambda *args, **kwargs: {
            "totalNum": 100,
            "productList": [{"spuId": "one", "title": "One", "status": 5}],
        }
        with self.assertRaises(ShopPrivateError):
            client.scan_products("sales", max_pages=None)

    def test_malformed_empty_scan_does_not_authorize_cache_removal(self):
        client = object.__new__(ShopPrivateClient)
        for response in ({"totalNum": None, "productList": []},
                         {"totalNum": 0, "productList": None},
                         {"totalNum": "bad", "productList": []}):
            with self.subTest(response=response):
                client.scan_page = lambda *args, **kwargs: response
                with self.assertRaises(ShopPrivateError):
                    client.scan_products("sales", max_pages=None)

    def test_cookie_only_delete_and_delisting_use_merchant_api(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            store_id = db.add_store("Fixture", "wx-fixture", "")
            store = db.get_store(store_id)
            product = ShopPrivateClient._parse_product(
                {"spuId": "one", "title": "One", "status": 5})
            product.store_id = store_id
            db.upsert_product(product)
            service = SyncService(db)
            service.platform_write_authorizer = (
                lambda _app_id, _action: None
            )
            with patch("weimi_manager.service.ShopPrivateClient") as factory:
                private = factory.return_value
                private.batch_product_action.return_value = {"success": True}
                result = service.set_products_listing(store, ["one"], False)
                self.assertEqual(result.success, 1)
                private.batch_product_action.assert_called_with(["one"], "offshelf")
                self.assertEqual(db.get_product(store_id, "one")["status"], 11)
                service.product_write(store, "delete", product_id="one")
                private.batch_product_action.assert_called_with(["one"], "recycle")
                self.assertIsNone(db.get_product(store_id, "one"))

    def test_cancelled_scan_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            store_id = db.add_store("Fixture", "wx-fixture", "")
            product = ShopPrivateClient._parse_product(
                {"spuId": "one", "title": "One", "status": 5})
            product.store_id = store_id
            db.upsert_product(product)
            cancelled = threading.Event()
            cancelled.set()
            with patch("weimi_manager.service.ShopPrivateClient") as factory:
                factory.return_value.scan_products.return_value = []
                result = SyncService(db).sync_private_product_tab(
                    db.get_store(store_id), "sales", max_pages=None, cancel_event=cancelled)
                self.assertTrue(result.cancelled)
                self.assertIsNotNone(db.get_product(store_id, "one"))
