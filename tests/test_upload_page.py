from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
import uuid
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock, patch

from weimi_manager.db import Database
from weimi_manager.models import Product, Store
from weimi_manager.ui.theme import apply_theme
from weimi_manager.ui.upload_page import (
    CategoryApprovalPending,
    TemplateRuleSkip,
    UploadPage,
    _definitive_upload_failure_message,
    _filter_batch_rows,
    _is_duplicate_upload,
    _normalize_duplicate_filter_mode,
    _normalize_source_payload,
    _recover_upload_tasks,
    _retry_upload_after_category_open,
    _ScrollableBody,
    _stores_matching_group_names,
    _upload_source_relation,
)
from weimi_manager.wechat_api import WechatApiError


class UploadPageContractTests(unittest.TestCase):
    def test_1688_upload_keeps_source_code_and_creates_sku_mapping(
        self,
    ) -> None:
        payload = _normalize_source_payload(
            {
                "platform": "1688",
                "offer_id": "1098691125768",
                "source_url": (
                    "https://detail.1688.com/offer/1098691125768.html"
                ),
                "title": "上家测试商品",
                "head_imgs": ["https://example.test/main.jpg"],
                "skus": [
                    {
                        "out_sku_id": "1688-SKU-RED",
                        "sale_price": 1000,
                        "stock_num": 10,
                        "sku_attrs": [
                            {"attr_key": "颜色", "attr_value": "红色"}
                        ],
                    }
                ],
            }
        )
        self.assertEqual(payload["out_product_id"], "1098691125768")
        item = {
            "source_product_id": "1098691125768",
            "title": payload["title"],
            "payload": payload,
            "raw": {"offer_id": "1098691125768"},
        }
        product = {
            "raw_json": json.dumps(
                {
                    "skus": [
                        {
                            "sku_id": "WX-SKU-RED",
                            "sale_price": 1000,
                            "sku_attrs": [
                                {
                                    "attr_key": "颜色",
                                    "attr_value": "红色",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }

        source_link, mappings = _upload_source_relation(item, product)

        self.assertIsNotNone(source_link)
        assert source_link is not None
        self.assertEqual(
            source_link["source_product_id"],
            "1098691125768",
        )
        self.assertEqual(mappings[0]["store_sku_id"], "WX-SKU-RED")
        self.assertEqual(
            mappings[0]["purchase_sku_id"],
            "1688-SKU-RED",
        )

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "upload-source.db")
            store_id = db.add_store("上货店铺", "wx-upload-source", "secret")
            db.upsert_product(
                Product(
                    store_id=store_id,
                    store_name="上货店铺",
                    product_id="WX-PRODUCT-1",
                    title="上家测试商品",
                    status=5,
                    status_text="销售中",
                    edit_status=0,
                    min_price=1500,
                    sku_count=1,
                    stock_total=10,
                    spu_code="",
                    out_product_id="1098691125768",
                    head_img="https://example.test/main.jpg",
                    raw=json.loads(product["raw_json"]),
                )
            )
            db.commit_upload_task_result(
                "[]",
                store_id,
                "1098691125768",
                "WX-PRODUCT-1",
                "上家测试商品",
                source_link=source_link,
                sku_mappings=mappings,
            )

            links = db.list_product_purchase_links(
                store_id,
                "WX-PRODUCT-1",
            )
            saved_mappings = db.list_product_sku_mappings(
                store_id,
                "WX-PRODUCT-1",
            )

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["source_product_id"], "1098691125768")
        self.assertEqual(len(saved_mappings), 1)
        self.assertEqual(
            saved_mappings[0]["purchase_sku_id"],
            "1688-SKU-RED",
        )

    def test_missing_category_permission_applies_category_and_retries_upload(
        self,
    ) -> None:
        upload_calls = []

        def upload():
            upload_calls.append("upload")
            if len(upload_calls) == 1:
                raise WechatApiError("商品无该类目权限", code=6600145)
            return {"product_id": "product-1"}

        class CategoryClient:
            def __init__(self) -> None:
                self.applied: list[list[str]] = []

            def category_relation_statuses(self, _category_ids):
                return {}

            def batch_add_categories(self, category_ids):
                self.applied.append(list(category_ids))
                return {"success": True}

        category_client = CategoryClient()
        result = _retry_upload_after_category_open(
            {
                "cats": [{"cat_id": "101"}],
                "cats_v2": [{"cat_id": "101"}],
            },
            upload,
            category_client,
        )

        self.assertEqual(result["product_id"], "product-1")
        self.assertEqual(category_client.applied, [["101"]])
        self.assertEqual(upload_calls, ["upload", "upload"])

    def test_category_under_review_stops_retry_and_keeps_categories_for_resume(
        self,
    ) -> None:
        class CategoryClient:
            def category_relation_statuses(self, category_ids):
                return {
                    str(category_id): "审核中"
                    for category_id in category_ids
                }

            def batch_add_categories(self, _category_ids):
                return {"success": True}

        with self.assertRaises(CategoryApprovalPending) as raised:
            _retry_upload_after_category_open(
                {"cats": [{"cat_id": "101"}]},
                lambda: (_ for _ in ()).throw(
                    WechatApiError("商品无该类目权限", code=6600145)
                ),
                CategoryClient(),
            )

        self.assertEqual(raised.exception.category_ids, ["101"])
        self.assertEqual(raised.exception.statuses, {"101": "审核中"})

    def test_automatic_category_retry_resumes_waiting_items(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.statuses = []

            def set_status(self, message, **_kwargs) -> None:
                self.statuses.append(message)

        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [
            {
                "status": "needs_category",
                "items": [
                    {
                        "status": "waiting_category",
                        "message": "等待类目生效",
                    }
                ],
            }
        ]
        page._running = False
        page._run_requested = False
        page._category_retry_job = None
        page._start_tasks = Mock()

        page._auto_retry_category_tasks()

        page._start_tasks.assert_called_once_with(automatic=True)
        self.assertEqual(
            page.window.statuses,
            ["正在检查已申请类目并自动恢复上货…"],
        )

    def test_group_selection_includes_all_stores_in_selected_groups(
        self,
    ) -> None:
        stores = [
            SimpleNamespace(id=1, group_name="予安"),
            SimpleNamespace(id=2, group_name="予安"),
            SimpleNamespace(id=3, group_name="盛达"),
            SimpleNamespace(id=4, group_name=""),
        ]

        selected = _stores_matching_group_names(
            stores,
            ["予安", "未分组"],
        )

        self.assertEqual([store.id for store in selected], [1, 2, 4])

    def test_upload_store_dialog_selects_all_stores_in_multiple_groups(
        self,
    ) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "store-select.db")
            first = db.add_store("予安店铺一", "wx-group-a1", "secret", "予安")
            second = db.add_store("予安店铺二", "wx-group-a2", "secret", "予安")
            third = db.add_store("盛达店铺", "wx-group-b1", "secret", "盛达")
            db.create_store_group("空分组")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            page = UploadPage(root, FakeWindow(db))

            def drive_dialog() -> None:
                dialogs = [
                    widget
                    for widget in descendants(root)
                    if isinstance(widget, tk.Toplevel)
                    and widget.title() == "选择上传店铺"
                ]
                if not dialogs:
                    root.after(20, drive_dialog)
                    return
                dialog = dialogs[0]
                group_list = next(
                    widget
                    for widget in descendants(dialog)
                    if isinstance(widget, tk.Listbox)
                )
                selected_indices = [
                    index
                    for index in range(group_list.size())
                    if group_list.get(index) in {"予安", "盛达"}
                ]
                group_list.selection_set(selected_indices[0])
                group_list.selection_set(selected_indices[1])
                group_list.event_generate("<<ListboxSelect>>")
                dialog.update_idletasks()
                confirm = next(
                    widget
                    for widget in descendants(dialog)
                    if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "确定"
                )
                confirm.invoke()

            root.after(20, drive_dialog)
            try:
                selected = page._choose_stores()
                self.assertEqual(
                    {store.id for store in selected},
                    {first, second, third},
                )
            finally:
                root.destroy()

    def test_known_remote_rejections_are_not_reported_as_unknown(
        self,
    ) -> None:
        category_error = WechatApiError(
            "商品无该类目权限",
            code=6600145,
        )
        attribute_error = WechatApiError(
            "商品参数类目数量需要选择单位",
            code=6601121,
        )
        self.assertIn(
            "目标类目权限",
            _definitive_upload_failure_message(category_error),
        )
        self.assertIn(
            "类目属性数量格式",
            _definitive_upload_failure_message(attribute_error),
        )
        self.assertEqual(
            _definitive_upload_failure_message(
                WechatApiError("系统繁忙", code=-1)
            ),
            "",
        )
        self.assertEqual(
            _definitive_upload_failure_message(
                ConnectionError("网络中断")
            ),
            "",
        )
        for error in (
            WechatApiError("HTTP 500: upstream reset"),
            WechatApiError("网络请求失败: timed out"),
            WechatApiError("接口返回了无法解析的数据"),
        ):
            with self.subTest(error=str(error)):
                self.assertEqual(
                    _definitive_upload_failure_message(error),
                    "",
                    "无结构化错误码的传输失败必须进入待核实，不能自动重试",
                )

    def test_refresh_only_renders_active_upload_view(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(
                    root,
                    FakeWindow(Database(Path(tmp) / "refresh.db")),
                )
                page._show_view("products")
                with (
                    patch.object(page, "_render_templates") as templates,
                    patch.object(page, "_render_batch") as batch,
                    patch.object(page, "_render_tasks") as tasks,
                    patch.object(
                        page,
                        "_render_category_stores",
                    ) as categories,
                ):
                    page.refresh()
                templates.assert_not_called()
                batch.assert_called_once()
                tasks.assert_not_called()
                categories.assert_not_called()
            finally:
                root.destroy()

    def test_batch_filter_modes_cover_title_sku_stock_and_price(self) -> None:
        items = [
            {
                "batch_id": "a",
                "title": "夏季连衣裙",
                "price": 1999,
                "stock": 5,
                "platform": "微信小店",
                "payload": {"skus": [{"id": "SKU-RED-01"}]},
            },
            {
                "batch_id": "b",
                "title": "冬季棉服",
                "price": 8999,
                "stock": 80,
                "platform": "淘宝",
                "payload": {"skus": [{"id": "SKU-WINTER-88"}]},
            },
        ]

        def ids(**criteria):
            arguments = {
                "keyword": "",
                "minimum": "",
                "maximum": "",
                "platform": "全部平台",
            }
            arguments.update(criteria)
            return [
                row["batch_id"]
                for row in _filter_batch_rows(
                    items,
                    **arguments,
                )
            ]

        self.assertEqual(ids(mode="标题包含", keyword="连衣裙"), ["a"])
        self.assertEqual(ids(mode="SKU包含", keyword="winter-88"), ["b"])
        self.assertEqual(
            ids(mode="库存范围", minimum="1", maximum="10"),
            ["a"],
        )
        self.assertEqual(
            ids(mode="价格范围", minimum="50", maximum="100"),
            ["b"],
        )
        with self.assertRaises(ValueError):
            ids(mode="库存范围", minimum="20", maximum="10")

    def test_selection_survives_pages_and_inverts_all_items(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "selection.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": str(index),
                        "title": f"商品 {index}",
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "微信小店",
                    }
                    for index in range(450)
                ]
                page._render_batch()
                self.assertEqual(len(page.batch_tree.get_children()), 200)

                page.batch_tree.selection_set(("0",))
                page._update_batch_summary()
                self.assertEqual(page._selected_batch_ids, {"0"})
                page._change_batch_page(1)
                self.assertEqual(len(page.batch_tree.get_children()), 200)
                self.assertEqual(page._selected_batch_ids, {"0"})

                page._invert_batch_selection(all_items=True)
                self.assertEqual(len(page._selected_batch_ids), 449)
                self.assertNotIn("0", page._selected_batch_ids)

                page._select_batch_result(force=True)
                self.assertEqual(len(page._selected_batch_ids), 450)
                page._clear_batch_selection()
                self.assertEqual(page._selected_batch_ids, set())
            finally:
                root.destroy()

    def test_batch_render_repairs_missing_batch_ids(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "missing-ids.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "title": f"商品 {index}",
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "1688",
                    }
                    for index in range(175)
                ]
                page._render_batch()

                children = page.batch_tree.get_children()
                self.assertEqual(len(children), 175)
                self.assertEqual(len(set(children)), 175)
                self.assertTrue(
                    all(
                        str(row.get("batch_id") or "").strip()
                        for row in page._batch
                    )
                )
            finally:
                root.destroy()

    def test_import_automatically_selects_new_products(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "auto-select.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                parsed = [
                    {
                        "batch_id": f"row-{index}",
                        "title": f"商品 {index}",
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "1688",
                    }
                    for index in range(3)
                ]
                with patch.object(
                    page,
                    "_parse_package",
                    return_value=parsed,
                ):
                    page._import_files([Path("fixture.json")])

                self.assertEqual(
                    page._selected_batch_ids,
                    {"row-0", "row-1", "row-2"},
                )
                self.assertEqual(
                    len(page.batch_tree.selection()),
                    3,
                )
            finally:
                root.destroy()

    def test_batch_render_tolerates_mixed_price_and_stock_types(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "mixed-types.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": "price-text",
                        "title": "价格文本",
                        "price": "¥12.50",
                        "stock": "12",
                        "platform": "1688",
                    },
                    {
                        "batch_id": "bad-stock",
                        "title": "异常库存",
                        "price": 990,
                        "stock": {"unknown": True},
                        "platform": "1688",
                    },
                    {
                        "batch_id": "missing-price",
                        "title": "缺少价格",
                        "stock": None,
                        "platform": "其他",
                    },
                ]
                page._render_batch()

                self.assertEqual(
                    len(page.batch_tree.get_children()),
                    3,
                )
                self.assertEqual(
                    page.batch_tree.set("price-text", "price"),
                    "¥12.50",
                )
                self.assertEqual(
                    page.batch_tree.set("bad-stock", "stock"),
                    "0",
                )
            finally:
                root.destroy()

    def test_filter_change_prunes_hidden_selection_before_delete(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "filter-selection.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": str(index),
                        "title": title,
                        "source_product_id": f"ID-{index}",
                        "price": 1000,
                        "stock": 10,
                        "platform": "微信小店",
                    }
                    for index, title in enumerate(
                        ("连衣裙一", "连衣裙二", "连衣裙三", "外套"),
                        start=1,
                    )
                ]
                page._render_batch()
                page._select_batch_result(force=True)
                self.assertEqual(len(page._selected_batch_ids), 4)

                page.filter_mode_var.set("标题包含")
                page.filter_keyword_var.set("连衣裙")
                page._apply_batch_filter()
                self.assertEqual(
                    page._selected_batch_ids,
                    {"1", "2", "3"},
                )

                with patch(
                    "weimi_manager.ui.upload_page.messagebox.askyesno",
                    return_value=True,
                ) as confirm:
                    page._delete_selected_batch()
                self.assertIn("3 条商品", str(confirm.call_args))
                self.assertEqual(
                    [row["title"] for row in page._batch],
                    ["外套"],
                )
            finally:
                root.destroy()

    def test_import_filter_and_create_tasks_end_to_end(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db

            def set_status(self, *_args, **_kwargs) -> None:
                pass

        payload = {
            "products": [
                {
                    "id": "SOURCE-ZERO",
                    "title": "零库存商品",
                    "price": "10.00",
                    "stock": 0,
                    "payload": {"title": "零库存商品"},
                },
                {
                    "id": "SOURCE-FIVE",
                    "title": "五件库存商品",
                    "price": "20.00",
                    "stock": 5,
                    "payload": {"title": "五件库存商品"},
                },
                {
                    "id": "SOURCE-TEN",
                    "title": "十件库存商品",
                    "price": "30.00",
                    "stock": 10,
                    "payload": {"title": "十件库存商品"},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "flow.db")
            package = Path(tmp) / "products.json"
            package.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._import_files([package])
                self.assertEqual(len(page._batch), 3)
                page.filter_mode_var.set("库存范围")
                page.filter_min_var.set("1")
                page._render_batch()
                page._select_batch_result(force=True)
                page._templates = [
                    {
                        "name": "库存测试模板",
                        "price_multiplier": 1,
                        "price_offset": 0,
                        "stock": 100,
                    }
                ]
                page.template_var.set("库存测试模板")
                store = Store(
                    id=1,
                    name="测试店铺",
                    app_id="wx-test",
                    group_name="",
                    status="正常",
                    last_sync_at=None,
                    created_at="",
                )
                with patch.object(
                    page,
                    "_choose_stores",
                    return_value=[store],
                ):
                    page._create_tasks()
                self.assertEqual(len(page._tasks), 1)
                titles = {
                    item["title"]
                    for item in page._tasks[0]["items"]
                }
                self.assertEqual(
                    titles,
                    {"五件库存商品", "十件库存商品"},
                )
                saved_tasks = json.loads(
                    db.get_setting("upload_tasks", "[]")
                )
                self.assertEqual(
                    len(saved_tasks[0]["items"]),
                    2,
                )
            finally:
                root.destroy()

    def test_template_scroll_wheel_works_over_child_controls(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.geometry("500x260+0+0")
        apply_theme(root)
        try:
            scroller = _ScrollableBody(root)
            scroller.pack(fill="both", expand=True)
            first_child = None
            for index in range(20):
                child = ttk.Label(
                    scroller.body,
                    text=f"模板设置 {index}",
                )
                child.pack(anchor="w", pady=8)
                if first_child is None:
                    first_child = child
            root.update()
            scroller.canvas.configure(
                scrollregion=scroller.canvas.bbox("all")
            )
            root.update()
            before = scroller.canvas.yview()
            event = SimpleNamespace(
                widget=first_child,
                x_root=first_child.winfo_rootx() + 3,
                y_root=first_child.winfo_rooty() + 3,
                delta=-120,
            )
            scroller._on_mousewheel(event)
            root.update_idletasks()
            after = scroller.canvas.yview()
            self.assertGreater(after[0], before[0])
        finally:
            root.destroy()

    def test_rapid_template_wheel_burst_is_coalesced(self) -> None:
        try:
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.geometry("500x260+0+0")
        apply_theme(root)
        try:
            scroller = _ScrollableBody(root)
            scroller.pack(fill="both", expand=True)
            first_child = None
            for index in range(40):
                child = ttk.Label(
                    scroller.body,
                    text=f"快速滚动设置 {index}",
                )
                child.pack(anchor="w", pady=5)
                if first_child is None:
                    first_child = child
            root.update()
            event = SimpleNamespace(
                widget=first_child,
                x_root=first_child.winfo_rootx() + 2,
                y_root=first_child.winfo_rooty() + 2,
                delta=-120,
            )
            with patch.object(
                scroller.canvas,
                "yview_scroll",
            ) as scroll:
                for _index in range(120):
                    scroller._on_mousewheel(event)
                root.update_idletasks()
            scroll.assert_called_once_with(120, "units")
        finally:
            root.destroy()

    def test_template_scrollbar_drag_is_coalesced(self) -> None:
        try:
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
        except tk.TclError as exc:
            self.skipTest(str(exc))
        apply_theme(root)
        try:
            scroller = _ScrollableBody(root)
            scroller.pack(fill="both", expand=True)
            with patch.object(scroller.canvas, "yview") as yview:
                for index in range(80):
                    scroller._on_scrollbar(
                        "moveto",
                        str(index / 79),
                    )
                time.sleep(0.03)
                root.update()
            self.assertEqual(yview.call_count, 1)
            yview.assert_called_with("1.0")
        finally:
            root.destroy()

    def test_batch_stock_filter_delete_selection_and_zero_stock_guard(
        self,
    ) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db
                self.messages: list[str] = []

            def set_status(self, message, **_kwargs) -> None:
                self.messages.append(message)

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "upload.db")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            try:
                page = UploadPage(root, FakeWindow(db))
                page._batch = [
                    {
                        "batch_id": "zero",
                        "title": "零库存商品",
                        "source_product_id": "SOURCE-ZERO",
                        "price": 100,
                        "stock": 0,
                        "platform": "微信小店",
                    },
                    {
                        "batch_id": "five",
                        "title": "五件库存商品",
                        "source_product_id": "SOURCE-FIVE",
                        "price": 200,
                        "stock": 5,
                        "platform": "微信小店",
                    },
                    {
                        "batch_id": "ten",
                        "title": "十件库存商品",
                        "source_product_id": "SOURCE-TEN",
                        "price": 300,
                        "stock": 10,
                        "platform": "微信小店",
                    },
                ]
                page.filter_mode_var.set("库存范围")
                page.filter_min_var.set("1")
                page.filter_max_var.set("")
                page._render_batch()
                self.assertEqual(
                    set(page.batch_tree.get_children()),
                    {"five", "ten"},
                )

                page.filter_min_var.set("0")
                page.filter_max_var.set("0")
                page._render_batch()
                self.assertEqual(
                    set(page.batch_tree.get_children()),
                    {"zero"},
                )

                page.filter_mode_var.set("全部商品")
                page.filter_min_var.set("")
                page.filter_max_var.set("")
                page._render_batch()
                page.batch_tree.selection_set(("zero",))
                with patch(
                    "weimi_manager.ui.upload_page.messagebox.showwarning"
                ) as warning:
                    page._create_tasks()
                warning.assert_called_once()
                self.assertEqual(page._tasks, [])

                page.batch_tree.selection_set(("zero", "ten"))
                page._update_batch_summary()
                self.assertEqual(
                    page.batch_tree.set("zero", "selected"),
                    "✓",
                )
                self.assertEqual(
                    page.batch_tree.set("five", "selected"),
                    "",
                )
                with patch(
                    "weimi_manager.ui.upload_page.messagebox.askyesno",
                    return_value=True,
                ):
                    page._delete_selected_batch()
                self.assertEqual(
                    [row["batch_id"] for row in page._batch],
                    ["five"],
                )
                saved = json.loads(db.get_setting("upload_batch", "[]"))
                self.assertEqual(
                    [row["batch_id"] for row in saved],
                    ["five"],
                )
            finally:
                root.destroy()

    def test_parse_json_package_and_apply_template(self) -> None:
        payload = {
            "products": [
                {
                    "title": "测试商品",
                    "price": "9.90",
                    "stock": 12,
                    "payload": {
                        "title": "测试商品",
                        "cats": [{"cat_id": "10000111"}],
                        "head_imgs": ["https://example.test/a.jpg"],
                        "skus": [{"sale_price": 990, "stock_num": 12}],
                    },
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "products.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "测试商品")
        self.assertEqual(rows[0]["price"], 990)
        page = object.__new__(UploadPage)
        result = page._upload_payload(
            rows[0],
            {
                "title_prefix": "【新品】",
                "title_suffix": " 包邮",
                "price_multiplier": 1.1,
                "price_offset": 100,
                "stock": 30,
            },
        )
        self.assertEqual(result["title"], "【新品】测试商品 包邮")
        self.assertEqual(result["skus"][0]["sale_price"], 1189)
        self.assertEqual(result["skus"][0]["stock_num"], 30)

    def test_parse_tsv_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "products.tsv"
            path.write_text(
                "title\tprice\tstock\tplatform\n"
                "测试商品\t19.90\t8\t淘宝\n",
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 1990)
        self.assertEqual(rows[0]["platform"], "淘宝")

    def test_parse_taobao_source_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "658035244243.txt"
            path.write_text(
                json.dumps(
                    {
                        "productId": "658035244243",
                        "productName": "黑色魔术裤",
                        "priceLow": "39.00",
                        "itemImgs": ["//img.alicdn.com/example.jpg"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "黑色魔术裤")
        self.assertEqual(rows[0]["source_product_id"], "658035244243")
        self.assertEqual(rows[0]["price"], 3900)
        self.assertEqual(rows[0]["platform"], "淘宝")

    def test_parse_wechat_source_package_price_and_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "[10000024298905].txt"
            path.write_text(
                json.dumps(
                    {
                        "uniqueId": "10000024298905",
                        "title": "防晒帽",
                        "skuList": {
                            "skuInfo": [
                                {"price": 2299},
                                {"price": 1999},
                            ]
                        },
                        "totalStockNum": "12",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "防晒帽")
        self.assertEqual(rows[0]["source_product_id"], "10000024298905")
        self.assertEqual(rows[0]["price"], 1999)
        self.assertEqual(rows[0]["stock"], 12)
        self.assertEqual(rows[0]["platform"], "微信小店")

    def test_missing_source_ids_use_stable_distinct_fingerprints(self) -> None:
        payload = {
            "products": [
                {
                    "title": "无 ID 商品甲",
                    "platform": "淘宝",
                    "price": "12.00",
                    "specs": {"颜色": ["黑色"]},
                },
                {
                    "title": "无 ID 商品乙",
                    "platform": "淘宝",
                    "price": "15.00",
                    "specs": {"颜色": ["白色"]},
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "同一数据包.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            first = UploadPage._parse_package(None, path)
            second = UploadPage._parse_package(None, path)

        first_ids = [row["source_product_id"] for row in first]
        second_ids = [row["source_product_id"] for row in second]
        self.assertEqual(len(set(first_ids)), 2)
        self.assertNotIn("同一数据包", first_ids)
        self.assertEqual(first_ids, second_ids)
        self.assertTrue(
            all(source_id.startswith("source-fp-") for source_id in first_ids)
        )

    def test_exactly_duplicated_missing_ids_are_stable_and_distinct(self) -> None:
        product = {
            "title": "完全相同的无 ID 商品",
            "platform": "微信小店",
            "price": "9.90",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text(
                json.dumps(
                    {"products": [product, product]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            first = UploadPage._parse_package(None, path)
            second = UploadPage._parse_package(None, path)

        first_ids = [row["source_product_id"] for row in first]
        second_ids = [row["source_product_id"] for row in second]
        self.assertEqual(len(set(first_ids)), 2)
        self.assertEqual(first_ids, second_ids)
        self.assertTrue(first_ids[1].endswith("-2"))

    def test_same_missing_id_product_across_files_gets_same_fingerprint(
        self,
    ) -> None:
        product = {
            "title": "跨文件相同商品",
            "platform": "淘宝",
            "price": "18.80",
            "specs": {"颜色": ["米白"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_path = root / "第一批.json"
            second_path = root / "第二批.json"
            text = json.dumps(product, ensure_ascii=False)
            first_path.write_text(text, encoding="utf-8")
            second_path.write_text(text, encoding="utf-8")
            first = UploadPage._parse_package(None, first_path)
            second = UploadPage._parse_package(None, second_path)

        self.assertEqual(
            first[0]["source_product_id"],
            second[0]["source_product_id"],
        )

    def test_raw_taobao_package_builds_wechat_upload_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "taobao.txt"
            path.write_text(
                json.dumps(
                    {
                        "productId": "TB-1",
                        "productName": "高腰休闲裤",
                        "priceLow": "20.00",
                        "categoryId": "162201",
                        "categoryName": "女装/女士精品/裤子/休闲裤",
                        "itemImgs": [
                            "//img.alicdn.com/example-1.jpg",
                            "//img.alicdn.com/example-2.jpg",
                            "//img.alicdn.com/example-3.jpg",
                        ],
                        "specs": {
                            "颜色分类": {
                                "1": ["黑色", ""],
                            },
                            "尺码": {
                                "2": ["M", ""],
                            },
                        },
                        "skus": [
                            {
                                "price": "2000",
                                "quantity": "12",
                                "id": "1;2",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        result = object.__new__(UploadPage)._upload_payload(
            rows[0],
            {"price_multiplier": 1.5, "price_offset": 0, "stock": 1000},
        )
        self.assertEqual(result["title"], "高腰休闲裤")
        self.assertEqual(
            [item["cat_id"] for item in result["cats"]],
            ["6033", "6057", "547675"],
        )
        self.assertEqual(result["head_imgs"][0], "https://img.alicdn.com/example-1.jpg")
        self.assertEqual(result["listing"], 1)
        self.assertEqual(result["skus"][0]["sale_price"], 3000)
        self.assertEqual(result["skus"][0]["stock_num"], 1000)
        self.assertEqual(
            {item["attr_key"] for item in result["skus"][0]["sku_attrs"]},
            {"颜色", "规格"},
        )

    def test_template_controls_immediate_listing_and_draft(self) -> None:
        page = object.__new__(UploadPage)
        item = {
            "title": "发布方式测试",
            "price": 100,
            "stock": 5,
            "payload": {
                "title": "发布方式测试",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 100, "stock_num": 5}],
            },
        }
        immediate = page._upload_payload(
            item,
            {"name": "立即上架", "price_multiplier": 1},
        )
        draft = page._upload_payload(
            item,
            {
                "name": "草稿",
                "price_multiplier": 1,
                "save_as_draft": True,
            },
        )
        delayed = page._upload_payload(
            item,
            {
                "name": "延迟",
                "price_multiplier": 1,
                "listing_mode": "delayed",
            },
        )
        self.assertEqual(immediate["listing"], 1)
        self.assertEqual(draft["listing"], 0)
        self.assertEqual(delayed["listing"], 0)

    def test_cloud_violation_type_filter_does_not_subtract_list_and_set(
        self,
    ) -> None:
        class FakeDb:
            @staticmethod
            def violation_blacklist_ids():
                return set()

            @staticmethod
            def get_setting(key, default=""):
                if key == "violation_library":
                    return json.dumps(
                        [
                            {
                                "product_id": "SOURCE-BAD",
                                "title": "明确违规商品",
                                "reason": "明确违规",
                            }
                        ],
                        ensure_ascii=False,
                    )
                return default

        page = object.__new__(UploadPage)
        page.db = FakeDb()
        for platform in ("1688", "淘宝", "微信小店"):
            with self.subTest(platform=platform):
                item = {
                    "source_product_id": "SOURCE-BAD",
                    "title": "明确违规商品",
                    "price": 100,
                    "stock": 5,
                    "payload": {
                        "title": "明确违规商品",
                        "platform": platform,
                        "cats": [{"cat_id": "10000111"}],
                        "head_imgs": ["https://example.test/a.jpg"],
                        "skus": [
                            {"sale_price": 100, "stock_num": 5}
                        ],
                    },
                }

                with self.assertRaises(TemplateRuleSkip):
                    page._upload_payload(
                        item,
                        {
                            "name": "云违规过滤",
                            "price_multiplier": 1,
                            "cloud_violation": True,
                            "cloud_violation_types": "明确违规",
                        },
                    )

    def test_duplicate_filter_defaults_to_store_and_can_be_disabled(
        self,
    ) -> None:
        self.assertEqual(_normalize_duplicate_filter_mode({}), "store")
        self.assertEqual(
            _normalize_duplicate_filter_mode(
                {"duplicate_filter_mode": "computer"}
            ),
            "computer",
        )
        self.assertEqual(
            _normalize_duplicate_filter_mode(
                {"duplicate_filter_mode": "off"}
            ),
            "off",
        )
        self.assertEqual(
            _normalize_duplicate_filter_mode(
                {
                    "duplicate_filter_mode": "off",
                    "skip_duplicate": False,
                }
            ),
            "off",
        )

    def test_duplicate_filter_scopes_are_isolated(self) -> None:
        by_store = {1: {"SOURCE-A"}, 2: {"SOURCE-B"}}
        by_computer = {"SOURCE-A", "SOURCE-B"}
        self.assertTrue(
            _is_duplicate_upload(
                "store",
                "SOURCE-A",
                1,
                by_store,
                by_computer,
            )
        )
        self.assertFalse(
            _is_duplicate_upload(
                "store",
                "SOURCE-A",
                2,
                by_store,
                by_computer,
            )
        )
        self.assertTrue(
            _is_duplicate_upload(
                "computer",
                "SOURCE-A",
                2,
                by_store,
                by_computer,
            )
        )
        self.assertFalse(
            _is_duplicate_upload(
                "off",
                "SOURCE-A",
                1,
                by_store,
                by_computer,
            )
        )

    def test_upload_payload_normalizes_string_categories(self) -> None:
        page = object.__new__(UploadPage)
        result = page._upload_payload(
            {
                "title": "字符串类目测试",
                "price": 100,
                "stock": 1,
                "payload": {
                    "title": "字符串类目测试",
                    "cats": ["6033", "6093", "6098"],
                    "cats_v2": ["10000111", "10000114", "6098"],
                    "head_imgs": [
                        "https://example.test/1.jpg",
                        "https://example.test/2.jpg",
                        "https://example.test/3.jpg",
                    ],
                    "skus": [{"sale_price": 100, "stock_num": 1}],
                },
            },
            {"price_multiplier": 1, "stock": 1},
        )
        self.assertEqual(
            result["cats"],
            [
                {"cat_id": "6033"},
                {"cat_id": "6093"},
                {"cat_id": "6098"},
            ],
        )
        self.assertEqual(
            result["cats_v2"],
            [
                {"cat_id": "10000111"},
                {"cat_id": "10000114"},
                {"cat_id": "6098"},
            ],
        )

    def test_upload_tasks_run_products_concurrently(self) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 7, "name": "并发测试店"},
        )()
        state = {
            "active": 0,
            "max_active": 0,
            "lock": threading.Lock(),
        }

        class FakeService:
            @staticmethod
            def client_for(_store):
                return object()

            @staticmethod
            def product_write(
                _store,
                _action,
                _payload,
                client=None,
                listing_mode="",
            ):
                assert client is not None
                assert listing_mode == "immediate"
                with state["lock"]:
                    state["active"] += 1
                    state["max_active"] = max(
                        state["max_active"],
                        state["active"],
                    )
                try:
                    time.sleep(0.04)
                finally:
                    with state["lock"]:
                        state["active"] -= 1
                return {
                    "errcode": 0,
                    "product_id": f"P-{uuid.uuid4().hex[:6]}",
                }

        class FakeWindow:
            service = FakeService()
            refreshed_store_ids: set[int] = set()

            def set_status(self, *_args, **_kwargs) -> None:
                pass

            def store_data_changed(self) -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            def run_task(
                self,
                work,
                done,
                _error,
                progress=None,
                log_title="",
            ) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        class FakeStorePage:
            @staticmethod
            def refresh_after_upload(store_ids):
                FakeWindow.refreshed_store_ids.update(store_ids)

        items = [
            {
                "title": f"商品{index}",
                "payload": {"title": f"商品{index}"},
                "source_product_id": f"SOURCE-{index}",
                "status": "pending",
                "message": "",
                "product_id": "",
            }
            for index in range(12)
        ]
        task = {
            "id": "task-concurrent",
            "store_id": 7,
            "store_name": "并发测试店",
            "template": {
                "name": "并发模板",
                "skip_duplicate": True,
                "listing_mode": "immediate",
            },
            "status": "pending",
            "items": items,
            "message": "等待开始",
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.window.store_page = FakeStorePage()
        page.db = type(
            "FakeDb",
            (),
            {
                "get_store": staticmethod(lambda _store_id: store),
                "record_upload_history": staticmethod(
                    lambda *_args, **_kwargs: None
                ),
                "uploaded_source_ids": staticmethod(
                    lambda _store_id=None: set()
                ),
            },
        )()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._save_tasks = lambda: None
        page._render_tasks = lambda: None
        page._progress = lambda *_args: None
        page._upload_payload = lambda item, _template: dict(
            item["payload"]
        )
        with (
            patch.dict(os.environ, {"WEIMI_UPLOAD_WORKERS": "4"}),
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ) as warning,
        ):
            page._start_tasks()
        if warning.called:
            self.fail(
                "unexpected warning: "
                + repr(
                    [str(value) for value in warning.call_args.args]
                )
            )
        self.assertGreaterEqual(state["max_active"], 2)
        self.assertTrue(
            all(item["status"] == "success" for item in items),
            [(item["status"], item.get("message")) for item in items],
        )
        self.assertEqual(task["status"], "completed")
        self.assertEqual(FakeWindow.refreshed_store_ids, {7})

    def test_upload_task_auto_applies_missing_category_before_retry(
        self,
    ) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 9, "name": "类目自动开通店"},
        )()
        product_attempts = []
        category_calls = []

        class FakeService:
            @staticmethod
            def client_for(_store):
                return object()

            @staticmethod
            def product_write(
                _store,
                _action,
                _payload,
                client=None,
                listing_mode="",
            ):
                product_attempts.append(listing_mode)
                if len(product_attempts) == 1:
                    raise WechatApiError(
                        "商品无该类目权限",
                        code=6600145,
                    )
                return {"errcode": 0, "product_id": "P-CATEGORY"}

        class FakeCategoryClient:
            def __init__(self, _db, _store) -> None:
                pass

            @staticmethod
            def category_relation_statuses(_category_ids):
                return {}

            @staticmethod
            def batch_add_categories(category_ids):
                category_calls.append(tuple(category_ids))
                return {"success": True}

        class FakeWindow:
            service = FakeService()
            diagnostics = None

            def set_status(self, *_args, **_kwargs) -> None:
                pass

            def store_data_changed(self) -> None:
                pass

            def run_task(
                self,
                work,
                done,
                _error,
                progress=None,
                log_title="",
            ) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        class FakeStorePage:
            @staticmethod
            def refresh_after_upload(_store_ids):
                pass

        item = {
            "item_id": "category-item",
            "title": "类目自动开通商品",
            "source_product_id": "SOURCE-CATEGORY",
            "status": "pending",
            "payload": {
                "title": "类目自动开通商品",
                "cats": [{"cat_id": "101"}],
            },
        }
        task = {
            "id": "task-category",
            "store_id": 9,
            "store_name": "类目自动开通店",
            "template": {
                "name": "自动类目模板",
                "duplicate_filter_mode": "off",
            },
            "status": "pending",
            "items": [item],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.window.store_page = FakeStorePage()
        page.db = type(
            "FakeDb",
            (),
            {
                "get_store": staticmethod(lambda _store_id: store),
                "record_upload_history": staticmethod(
                    lambda *_args, **_kwargs: None
                ),
                "uploaded_source_ids": staticmethod(
                    lambda _store_id=None: set()
                ),
            },
        )()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._save_tasks = lambda: None
        page._render_tasks = lambda: None
        page._progress = lambda *_args: None
        page._upload_payload = lambda item_row, _template: dict(
            item_row["payload"]
        )
        with (
            patch(
                "weimi_manager.ui.upload_page.ShopPrivateClient",
                FakeCategoryClient,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
        ):
            page._start_tasks()

        self.assertEqual(category_calls, [("101",)])
        self.assertEqual(product_attempts, ["immediate", "immediate"])
        self.assertEqual(item["status"], "success")
        self.assertEqual(item["product_id"], "P-CATEGORY")

    def test_rejected_scheduler_does_not_leave_upload_running(self) -> None:
        class FakeTree:
            @staticmethod
            def selection():
                return ()

        class FakeWindow:
            def __init__(self) -> None:
                self.messages = []

            def set_status(self, message, **_kwargs) -> None:
                self.messages.append(message)

            def run_task(self, *_args, **_kwargs) -> bool:
                self.set_status("已有任务正在执行")
                return False

        task = {
            "id": "task-rejected",
            "store_id": 7,
            "store_name": "拒绝测试店",
            "template": {"name": "测试模板"},
            "status": "pending",
            "items": [
                {
                    "item_id": "item-rejected",
                    "title": "拒绝测试商品",
                    "source_product_id": "SOURCE-REJECTED",
                    "status": "pending",
                    "payload": {"title": "拒绝测试商品"},
                }
            ],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._save_tasks = Mock()
        page._render_tasks = Mock()
        page._progress = Mock()

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()

        self.assertFalse(page._running)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["items"][0]["status"], "pending")
        page._save_tasks.assert_not_called()
        self.assertIn("已有任务正在执行", page.window.messages)

    def test_refresh_does_not_replace_running_task_truth_source(self) -> None:
        original_task = {
            "id": "task-running",
            "store_id": 7,
            "store_name": "运行中店铺",
            "status": "running",
            "items": [
                {
                    "item_id": "item-success",
                    "title": "已完成商品",
                    "source_product_id": "SOURCE-SUCCESS",
                    "status": "success",
                    "product_id": "P_TEST_001",
                }
            ],
        }
        persisted_task = {
            "id": "task-running",
            "store_id": 7,
            "store_name": "运行中店铺",
            "status": "running",
            "items": [
                {
                    "item_id": "item-success",
                    "title": "已完成商品",
                    "source_product_id": "SOURCE-SUCCESS",
                    "status": "pending",
                    "product_id": "",
                }
            ],
        }

        class FakeDb:
            @staticmethod
            def get_setting(key, default):
                if key == "upload_tasks":
                    return json.dumps([persisted_task], ensure_ascii=False)
                return default

        page = object.__new__(UploadPage)
        page.db = FakeDb()
        page._tasks = [original_task]
        page._templates = []
        page._batch = []
        page._task_lock = threading.RLock()
        page._running = True
        page._run_requested = False
        page._render_templates = Mock()
        page._render_batch = Mock()
        page._render_tasks = Mock()
        page._render_category_stores = Mock()

        for _ in range(30):
            page.refresh()

        self.assertIs(page._tasks[0], original_task)
        self.assertEqual(
            page._tasks[0]["items"][0]["product_id"],
            "P_TEST_001",
        )

    def test_running_upload_cannot_clear_or_delete_tasks(self) -> None:
        task = {
            "id": "task-active",
            "status": "running",
            "items": [
                {
                    "item_id": "item-active",
                    "status": "submitting",
                }
            ],
        }

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        class FakeWindow:
            def __init__(self) -> None:
                self.messages = []

            def set_status(self, message, **_kwargs) -> None:
                self.messages.append(message)

        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = True
        page._run_requested = False
        page._paused = False
        page._save_tasks = Mock()
        page._render_tasks = Mock()

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ) as warning,
        ):
            page._clear_all_tasks()
            page._delete_tasks()

        self.assertEqual(page._tasks, [task])
        page._save_tasks.assert_not_called()
        self.assertGreaterEqual(warning.call_count, 2)
        self.assertFalse(
            any("已清空" in message for message in page.window.messages)
        )

    def test_pause_reports_in_flight_wait_not_stopped(self) -> None:
        page = object.__new__(UploadPage)
        page.window = Mock()
        page._paused = False

        page._pause_tasks()

        self.assertTrue(page._paused)
        page.window.set_status.assert_called_once()
        self.assertIn("当前商品处理结束后停止", page.window.set_status.call_args.args[0])

    def test_interrupted_upload_marks_unknown_results_for_verification(
        self,
    ) -> None:
        tasks = [
            {
                "id": "task-interrupted",
                "status": "running",
                "items": [
                    {
                        "title": "提交中商品",
                        "status": "submitting",
                    },
                    {
                        "title": "排队商品",
                        "status": "queued",
                    },
                    {
                        "item_id": "item-success",
                        "title": "已成功商品",
                        "status": "success",
                        "product_id": "P-SUCCESS",
                    },
                ],
            }
        ]

        changed = _recover_upload_tasks(tasks)

        self.assertTrue(changed)
        items = tasks[0]["items"]
        self.assertTrue(all(item.get("item_id") for item in items))
        self.assertEqual(items[0]["status"], "verification_pending")
        self.assertEqual(items[1]["status"], "paused")
        self.assertEqual(items[2]["status"], "success")
        self.assertEqual(items[2]["product_id"], "P-SUCCESS")
        self.assertEqual(tasks[0]["status"], "needs_verification")
        self.assertIn("待核实", tasks[0]["message"])

    def test_remote_success_with_failed_local_commit_is_verification_pending(
        self,
    ) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 9, "name": "落盘测试店"},
        )()

        class FakeService:
            calls = 0

            @staticmethod
            def client_for(_store):
                return object()

            @classmethod
            def product_write(cls, *_args, **_kwargs):
                cls.calls += 1
                return {
                    "errcode": 0,
                    "product_id": "P_REMOTE_OK",
                }

        class FakeDb:
            def __init__(self) -> None:
                self.snapshots = []
                self.history_calls = 0

            @staticmethod
            def get_store(_store_id):
                return store

            @staticmethod
            def uploaded_source_ids(_store_id):
                return set()

            def set_setting(self, key, value) -> None:
                if key == "upload_tasks":
                    self.snapshots.append(json.loads(value))

            def commit_upload_task_result(self, *_args, **_kwargs) -> None:
                raise OSError("simulated local disk failure")

            def record_upload_history(self, *_args, **_kwargs) -> None:
                self.history_calls += 1

        class FakeWindow:
            service = FakeService()

            def __init__(self) -> None:
                self.store_page = Mock()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def store_data_changed() -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def run_task(work, done, _error, progress=None, **_kwargs) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        item = {
            "item_id": "item-local-failure",
            "title": "远端成功本地失败",
            "source_product_id": "SOURCE-LOCAL-FAILURE",
            "status": "pending",
            "payload": {"title": "远端成功本地失败"},
        }
        task = {
            "id": "task-local-failure",
            "store_id": 9,
            "store_name": "落盘测试店",
            "template": {"name": "测试模板"},
            "status": "pending",
            "items": [item],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.db = FakeDb()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._run_requested = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._render_tasks = Mock()
        page._progress = Mock()
        page._upload_payload = lambda _item, _template: {"title": "测试"}

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()

        self.assertEqual(FakeService.calls, 1)
        self.assertEqual(item["product_id"], "P_REMOTE_OK")
        self.assertEqual(item["status"], "verification_pending")
        self.assertEqual(task["status"], "needs_verification")
        self.assertEqual(page.db.history_calls, 0)
        self.assertTrue(
            any(
                snapshot[0]["items"][0]["status"] == "submitting"
                for snapshot in page.db.snapshots
            )
        )
        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()
        self.assertEqual(FakeService.calls, 1)

    def test_upload_snapshot_and_history_commit_in_one_database_transaction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "upload-atomic.db")
            store_id = db.add_store("原子写入店", "wx-atomic", "secret")
            tasks = [
                {
                    "id": "task-atomic",
                    "status": "completed",
                    "items": [
                        {
                            "item_id": "item-atomic",
                            "status": "success",
                            "product_id": "P-ATOMIC",
                        }
                    ],
                }
            ]

            db.commit_upload_task_result(
                json.dumps(tasks, ensure_ascii=False),
                store_id,
                "SOURCE-ATOMIC",
                "P-ATOMIC",
                "原子写入商品",
            )

            self.assertEqual(
                json.loads(db.get_setting("upload_tasks", "[]")),
                tasks,
            )
            self.assertIn("SOURCE-ATOMIC", db.uploaded_source_ids(store_id))

    def test_prepare_for_close_persists_unknown_inflight_result(self) -> None:
        task = {
            "id": "task-closing",
            "status": "running",
            "items": [
                {
                    "item_id": "item-closing",
                    "status": "submitting",
                }
            ],
        }
        page = object.__new__(UploadPage)
        page._tasks = [task]
        page._task_lock = threading.RLock()
        page._paused = False
        page._save_tasks = Mock()

        page.prepare_for_close()

        self.assertTrue(page._paused)
        self.assertEqual(
            task["items"][0]["status"],
            "verification_pending",
        )
        page._save_tasks.assert_called_once_with()

    def test_network_error_after_submission_is_not_auto_retried(self) -> None:
        store = type(
            "StoreValue",
            (),
            {"id": 11, "name": "网络异常店"},
        )()

        class FakeService:
            calls = 0

            @staticmethod
            def client_for(_store):
                return object()

            @classmethod
            def product_write(cls, *_args, **_kwargs):
                cls.calls += 1
                raise TimeoutError("模拟远端超时")

        class FakeDb:
            snapshots = []

            @staticmethod
            def get_store(_store_id):
                return store

            @staticmethod
            def uploaded_source_ids(_store_id):
                return set()

            def set_setting(self, key, value) -> None:
                if key == "upload_tasks":
                    self.snapshots.append(json.loads(value))

        class FakeWindow:
            service = FakeService()

            def __init__(self) -> None:
                self.store_page = Mock()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def store_data_changed() -> None:
                pass

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                pass

            @staticmethod
            def run_task(work, done, _error, progress=None, **_kwargs) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0",)

        item = {
            "item_id": "item-timeout",
            "title": "超时商品",
            "source_product_id": "SOURCE-TIMEOUT",
            "status": "pending",
            "payload": {"title": "超时商品"},
        }
        task = {
            "id": "task-timeout",
            "store_id": 11,
            "store_name": "网络异常店",
            "template": {"name": "测试模板"},
            "status": "pending",
            "items": [item],
        }
        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.db = FakeDb()
        page._tasks = [task]
        page._task_rows = {"0": task}
        page.task_tree = FakeTree()
        page._running = False
        page._run_requested = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._render_tasks = Mock()
        page._progress = Mock()
        page._upload_payload = lambda _item, _template: {"title": "测试"}

        with (
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()
            page._start_tasks()

        self.assertEqual(FakeService.calls, 1)
        self.assertEqual(item["status"], "verification_pending")
        self.assertEqual(task["status"], "needs_verification")
        self.assertIn("结果未知", item["message"])

    def test_multi_store_upload_keeps_results_isolated(self) -> None:
        stores = {
            21: type("StoreValue", (), {"id": 21, "name": "店铺甲"})(),
            22: type("StoreValue", (), {"id": 22, "name": "店铺乙"})(),
        }
        state = {
            "calls": [],
            "active": 0,
            "max_active": 0,
            "lock": threading.Lock(),
        }

        class FakeService:
            @staticmethod
            def client_for(_store):
                return object()

            @staticmethod
            def product_write(store, _action, payload, **_kwargs):
                with state["lock"]:
                    state["calls"].append((store.id, payload["title"]))
                    state["active"] += 1
                    state["max_active"] = max(
                        state["max_active"],
                        state["active"],
                    )
                try:
                    time.sleep(0.03)
                finally:
                    with state["lock"]:
                        state["active"] -= 1
                return {
                    "errcode": 0,
                    "product_id": f"P-{store.id}-{payload['title']}",
                }

        class FakeDb:
            @staticmethod
            def get_store(store_id):
                return stores.get(store_id)

            @staticmethod
            def uploaded_source_ids(_store_id):
                return set()

            @staticmethod
            def set_setting(*_args, **_kwargs):
                return

            @staticmethod
            def commit_upload_task_result(*_args, **_kwargs):
                return

            @staticmethod
            def record_upload_history(*_args, **_kwargs):
                return

        class FakeWindow:
            service = FakeService()

            def __init__(self) -> None:
                self.store_page = Mock()

            @staticmethod
            def set_status(*_args, **_kwargs) -> None:
                return

            @staticmethod
            def store_data_changed() -> None:
                return

            @staticmethod
            def _post(*_args, **_kwargs) -> None:
                return

            @staticmethod
            def run_task(work, done, _error, progress=None, **_kwargs) -> bool:
                done(work(progress))
                return True

        class FakeTree:
            @staticmethod
            def selection():
                return ("0", "1")

        tasks = []
        for task_index, store_id in enumerate((21, 22)):
            tasks.append(
                {
                    "id": f"task-multi-{store_id}",
                    "store_id": store_id,
                    "store_name": stores[store_id].name,
                    "template": {"name": "多店测试"},
                    "status": "pending",
                    "items": [
                        {
                            "item_id": f"item-{store_id}-{item_index}",
                            "title": f"{stores[store_id].name}商品{item_index}",
                            "source_product_id": (
                                f"SOURCE-{store_id}-{item_index}"
                            ),
                            "status": "pending",
                            "payload": {
                                "title": (
                                    f"{stores[store_id].name}商品{item_index}"
                                )
                            },
                        }
                        for item_index in range(2)
                    ],
                }
            )

        page = object.__new__(UploadPage)
        page.window = FakeWindow()
        page.db = FakeDb()
        page._tasks = tasks
        page._task_rows = {"0": tasks[0], "1": tasks[1]}
        page.task_tree = FakeTree()
        page._running = False
        page._run_requested = False
        page._paused = False
        page._task_lock = threading.RLock()
        page._render_tasks = Mock()
        page._progress = Mock()
        page._upload_payload = lambda item, _template: dict(item["payload"])

        with (
            patch.dict(os.environ, {"WEIMI_UPLOAD_WORKERS": "2"}),
            patch(
                "weimi_manager.ui.upload_page.messagebox.askyesno",
                return_value=True,
            ),
            patch(
                "weimi_manager.ui.upload_page.messagebox.showwarning"
            ),
        ):
            page._start_tasks()

        self.assertGreaterEqual(state["max_active"], 2)
        self.assertEqual({store_id for store_id, _ in state["calls"]}, {21, 22})
        self.assertTrue(
            all(
                item["status"] == "success"
                for task in tasks
                for item in task["items"]
            )
        )
        self.assertEqual(
            {
                int(item["product_id"].split("-")[1])
                for task in tasks
                for item in task["items"]
            },
            {21, 22},
        )

    def test_taobao_missing_head_images_uses_sku_and_detail_images(
        self,
    ) -> None:
        result = _normalize_source_payload(
            {
                "productId": "TB-IMAGE-FALLBACK",
                "productName": "主图不足但详情图完整",
                "categoryId": "162201",
                "itemImgs": ["//img.alicdn.com/main-1.jpg"],
                "desc": (
                    '<p><img src="//img.alicdn.com/detail-1.jpg"></p>'
                    '<p><img src="//img.alicdn.com/detail-2.jpg"></p>'
                ),
                "specs": {
                    "颜色分类": {
                        "1": ["黑色", ""],
                    },
                },
                "skus": [
                    {
                        "price": "2000",
                        "quantity": "12",
                        "id": "1",
                        "skuImageUrl": "//img.alicdn.com/sku-black.jpg",
                    }
                ],
            }
        )
        self.assertEqual(
            result["head_imgs"],
            [
                "https://img.alicdn.com/main-1.jpg",
                "https://img.alicdn.com/sku-black.jpg",
                "https://img.alicdn.com/detail-1.jpg",
            ],
        )
        self.assertEqual(
            result["desc_info"]["imgs"][0],
            "https://img.alicdn.com/detail-1.jpg",
        )

    def test_taobao_detail_images_fill_missing_heads_without_sku_images(
        self,
    ) -> None:
        result = _normalize_source_payload(
            {
                "productId": "TB-DETAIL-FALLBACK",
                "productName": "详情图补主图",
                "categoryId": "162201",
                "itemImgs": ["//img.alicdn.com/main-1.jpg"],
                "desc": (
                    '<img src="//img.alicdn.com/detail-1.jpg">'
                    '<img src="//img.alicdn.com/detail-2.jpg">'
                ),
                "skus": [
                    {"price": "2000", "quantity": "12", "id": ""}
                ],
            }
        )
        self.assertEqual(len(result["head_imgs"]), 3)
        self.assertEqual(
            result["head_imgs"][1:],
            [
                "https://img.alicdn.com/detail-1.jpg",
                "https://img.alicdn.com/detail-2.jpg",
            ],
        )

    def test_raw_wechat_package_builds_wechat_upload_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wechat.txt"
            path.write_text(
                json.dumps(
                    {
                        "title": "测试防晒帽",
                        "headImg": [
                            "https://wst.example.test/hat-1.jpg",
                            "https://wst.example.test/hat-2.jpg",
                        ],
                        "category": [
                            {"catId": "6033"},
                            {"catId": "6093"},
                            {"catId": "549163"},
                        ],
                        "detail": {
                            "detailImg": [
                                "https://wst.example.test/detail-1.jpg"
                            ],
                            "desc": "详情",
                        },
                        "productSaleAttrs": [
                            {
                                "key": "颜色",
                                "items": [
                                    {
                                        "value": "米色",
                                        "skuIds": ["WX-SKU-1"],
                                    }
                                ],
                            }
                        ],
                        "skuList": {
                            "skuInfo": [
                                {
                                    "skuId": "WX-SKU-1",
                                    "price": 2000,
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rows = UploadPage._parse_package(None, path)
        result = object.__new__(UploadPage)._upload_payload(
            rows[0],
            {"price_multiplier": 1.5, "price_offset": 0, "stock": 1000},
        )
        self.assertEqual(result["title"], "测试防晒帽")
        self.assertEqual(
            [item["cat_id"] for item in result["cats"]],
            ["6033", "6093", "549163"],
        )
        self.assertEqual(result["head_imgs"][0], "https://wst.example.test/hat-1.jpg")
        self.assertEqual(
            result["desc_info"]["imgs"],
            ["https://wst.example.test/detail-1.jpg"],
        )
        self.assertEqual(result["skus"][0]["sale_price"], 3000)
        self.assertEqual(result["skus"][0]["stock_num"], 1000)
        self.assertEqual(
            result["skus"][0]["sku_attrs"][0]["attr_value"],
            "米色",
        )

    def test_package_files_recursively_finds_supported_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "淘宝数据").mkdir()
            (root / "微信数据").mkdir()
            taobao = root / "淘宝数据" / "a.txt"
            wechat = root / "微信数据" / "b.json"
            ignored = root / "微信数据" / "image.png"
            taobao.write_text("{}", encoding="utf-8")
            wechat.write_text("{}", encoding="utf-8")
            ignored.write_bytes(b"not-an-image")
            files = UploadPage._package_files(root)
        self.assertCountEqual(files, [taobao, wechat])

    def test_template_price_rules_title_and_attributes(self) -> None:
        page = object.__new__(UploadPage)
        item = {
            "title": "【促销】旧款棉柔巾",
            "price": 1000,
            "stock": 12,
            "payload": {
                "title": "【促销】旧款棉柔巾",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 1000, "stock_num": 12}],
            },
            "source_product_id": "source-1",
        }
        result = page._upload_payload(
            item,
            {
                "strip_title_brackets": True,
                "title_replace_rules": "旧款 = 新款",
                "fixed_price_enabled": True,
                "fixed_price": 1999,
                "final_price_decimals": 2,
                "stock": 30,
                "enable_custom_attributes": True,
                "attribute_rules": "保质期=1年\n适用人群=家庭",
                "delivery_type": "现货",
                "delivery_time": "48小时内发货",
            },
        )
        self.assertEqual(result["title"], "新款棉柔巾")
        self.assertEqual(result["skus"][0]["sale_price"], 1999)
        self.assertEqual(
            result["attrs"],
            [
                {"attr_key": "保质期", "attr_value": "1年"},
                {"attr_key": "适用人群", "attr_value": "家庭"},
            ],
        )

    def test_template_filter_skips_product(self) -> None:
        page = object.__new__(UploadPage)
        item = {
            "title": "买一送一测试商品",
            "price": 1000,
            "payload": {
                "title": "买一送一测试商品",
                "cats": [{"cat_id": "10000111"}],
                "head_imgs": ["https://example.test/a.jpg"],
                "skus": [{"sale_price": 1000, "stock_num": 12}],
            },
        }
        with self.assertRaises(TemplateRuleSkip):
            page._upload_payload(
                item,
                {"title_rules": "买一送一"},
            )

    def test_multi_store_category_scan_uses_one_reference_store(self) -> None:
        class FakeWindow:
            def __init__(self, db) -> None:
                self.db = db
                self.log_titles = []

            def set_status(self, *_args, **_kwargs) -> None:
                pass

            def run_task(
                self,
                work,
                done,
                _error,
                progress=None,
                log_title="",
            ) -> None:
                self.log_titles.append(log_title)
                done(work(progress))

        class FakeCategoryClient:
            scan_count = 0
            batch_calls = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.store_id = _args[1].id

            def category_candidates(self, _budget):
                type(self).scan_count += 1
                return [
                    {
                        "category_id": "101",
                        "name": "测试类目",
                        "path": "一级 / 测试类目",
                        "required_deposit": 100000,
                        "amount_to_pay": 0,
                        "deposit_balance": 200000,
                        "status": "可申请",
                    }
                ]

            def batch_add_categories(self, category_ids):
                type(self).batch_calls.append(
                    (self.store_id, tuple(category_ids))
                )
                return {"success": True}

            def category_relation_statuses(self, category_ids):
                return {
                    str(category_id): "审核中"
                    for category_id in category_ids
                }

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "ui.db")
            first = db.add_store("店铺一", "wx-first", "secret")
            second = db.add_store("店铺二", "wx-second", "secret")
            db.update_web_status(first, "已登录", "ok")
            db.update_web_status(second, "已登录", "ok")
            try:
                root = tk.Tk()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            root.withdraw()
            apply_theme(root)
            page = UploadPage(root, FakeWindow(db))
            page.category_mode_var.set("多店申请")
            page._render_category_stores()
            page.category_store_tree.selection_set(
                (str(first), str(second))
            )
            page.category_budget_var.set("1000")
            with patch(
                "weimi_manager.ui.upload_page.ShopPrivateClient",
                FakeCategoryClient,
            ):
                page._scan_category_candidates()
                root.update()
            self.assertEqual(FakeCategoryClient.scan_count, 1)
            self.assertEqual(len(page._category_candidates), 1)
            row = next(iter(page._category_candidates.values()))
            self.assertEqual(
                set(row["target_store_ids"]),
                {first, second},
            )
            with (
                patch(
                    "weimi_manager.ui.upload_page.ShopPrivateClient",
                    FakeCategoryClient,
                ),
                patch(
                    "weimi_manager.ui.upload_page.messagebox.askyesno",
                    return_value=True,
                ),
            ):
                page._submit_category_candidates()
                root.update()
            self.assertEqual(
                sorted(
                    (store_id, categories)
                    for store_id, categories in FakeCategoryClient.batch_calls
                ),
                sorted(
                    [
                        (first, ("101",)),
                        (second, ("101",)),
                    ]
                ),
            )
            self.assertEqual(
                page.category_result_tree.set("0", "status"),
                "审核中",
            )
            self.assertEqual(
                page.window.log_titles,
                ["扫描可申请类目", "提交类目审核"],
            )
            root.destroy()


if __name__ == "__main__":
    unittest.main()
