from __future__ import annotations

import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

from weimi_manager.db import Database
from weimi_manager.models import Store
from weimi_manager.service import SyncService
from weimi_manager.shop_private import ShopPrivateClient
from weimi_manager.ui.activity_center import ActivityCenterPage
from weimi_manager.ui.theme import apply_theme


@pytest.fixture(autouse=True)
def _allow_fake_private_writes(monkeypatch):
    original_init = SyncService.__init__

    def init(service, *args, **kwargs):
        original_init(service, *args, **kwargs)
        service.platform_write_authorizer = (
            lambda _app_id, _action: None
        )

    monkeypatch.setattr(SyncService, "__init__", init)


def test_activity_center_nine_discount_flows_to_bulk_import_template() -> None:
    class ActivityClient:
        def __init__(self, *_args, **_kwargs):
            pass

        @staticmethod
        def account_close_status():
            return 100

        @staticmethod
        def activity_eligible_products(_activity_id):
            return [
                {
                    "productId": "PRODUCT-ODD",
                    "skuId": "SKU-ODD",
                    "salePrice": "2005",
                    "commStockNum": "10",
                    "invalidString": "",
                }
            ]

        @staticmethod
        def activity_submitted_products(_activity_id):
            return []

        @staticmethod
        def activity_validate_products(_activity_id, items):
            return {
                "resultList": [
                    {
                        "product": {
                            "productId": item["productId"],
                            "skuId": item["skuId"],
                        },
                        "invalidString": "",
                        "invalidStringPrecheck": "",
                    }
                    for item in items
                ]
            }

    class WebSession:
        submitted_prices: list[float] = []

        @classmethod
        def bulk_register_activity_products(
            cls,
            _store,
            _activity_id,
            template_path: Path,
            expected_count: int,
        ):
            workbook = load_workbook(template_path, data_only=True)
            try:
                cls.submitted_prices.append(float(workbook.active["C2"].value))
            finally:
                workbook.close()
            return {"count": expected_count}

    class Window:
        def __init__(self, db: Database) -> None:
            self.db = db
            self.service = SyncService(db)
            self.web_session = WebSession()
            self.messages: list[str] = []

        def set_status(self, message: str, **_kwargs) -> None:
            self.messages.append(message)

        @staticmethod
        def store_data_changed() -> None:
            return None

        @staticmethod
        def run_task(work, done, _error, progress=None, **_kwargs):
            done(work(progress))
            return True

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        import pytest

        pytest.skip(str(exc))
    root.withdraw()
    apply_theme(root)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "nine-discount.db")
        store_id = db.add_store(
            "九折活动测试店",
            "wx-nine-discount-ui",
            "secret",
        )
        db.update_web_status(store_id, "已登录", "ok")
        store = db.get_store(store_id)
        assert store is not None
        try:
            window = Window(db)
            page = ActivityCenterPage(root, window)
            page._stores = {store_id: store}
            page.store_tree.insert(
                "",
                "end",
                iid=str(store_id),
                values=(store.name, store.web_status, "", 1, ""),
            )
            page.store_tree.selection_set(str(store_id))
            page.activity_id_var.set("1323")
            page.discount_var.set("9.0")
            page.min_discount_var.set("0.00")
            with (
                patch(
                    "weimi_manager.service.ShopPrivateClient",
                    ActivityClient,
                ),
                patch(
                    "weimi_manager.ui.activity_center.messagebox.askyesno",
                    return_value=True,
                ),
                patch(
                    "weimi_manager.ui.activity_center.messagebox.showinfo"
                ),
            ):
                page._preview_activity()
                page._submit_activity()
        finally:
            root.destroy()

    assert WebSession.submitted_prices == [18.05]


def test_activity_center_uses_required_name_and_six_tabs() -> None:
    class Window:
        def __init__(self, db: Database) -> None:
            self.db = db
            self.service = SyncService(db)
            self.web_session = object()

        @staticmethod
        def set_status(*_args, **_kwargs) -> None:
            return None

        @staticmethod
        def run_task(*_args, **_kwargs) -> bool:
            return False

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        import pytest

        pytest.skip(str(exc))
    root.withdraw()
    apply_theme(root)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "activity-center-tabs.db")
        try:
            page = ActivityCenterPage(root, Window(db))
            assert page.title == "活动中心"
            assert page.limited_discount_var.get() == "8.5"
            assert page.limited_min_discount_var.get() == "1.00"
            assert page.limited_duration_var.get() == "168"
            page.snapup_tree.insert("", "end", iid="1:101", values=("店铺",))
            page.snapup_tree.insert("", "end", iid="1:102", values=("店铺",))
            page._select_all_snapup()
            assert set(page.snapup_tree.selection()) == {"1:101", "1:102"}
            page._clear_snapup_selection()
            assert page.snapup_tree.selection() == ()
            assert [
                page.notebook.tab(tab_id, "text")
                for tab_id in page.notebook.tabs()
            ] == [
                "平台活动",
                "限时限量购活动",
                "优惠券活动",
                "限时限量购续期",
                "优惠券管理",
                "执行记录",
            ]
        finally:
            root.destroy()


def test_activity_center_replaces_legacy_limited_defaults() -> None:
    class Window:
        def __init__(self, db: Database) -> None:
            self.db = db
            self.service = SyncService(db)
            self.web_session = object()

        @staticmethod
        def set_status(*_args, **_kwargs) -> None:
            return None

        @staticmethod
        def run_task(*_args, **_kwargs) -> bool:
            return False

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    root.withdraw()
    apply_theme(root)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "activity-center-defaults.db")
        db.set_setting("limited_activity_min_discount", "0.00")
        db.set_setting("limited_activity_duration_hours", "24")
        try:
            page = ActivityCenterPage(root, Window(db))
            assert page.limited_discount_var.get() == "8.5"
            assert page.limited_min_discount_var.get() == "1.00"
            assert page.limited_duration_var.get() == "168"
        finally:
            root.destroy()


def test_navigation_uses_activity_center_name() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "weimi_manager"
        / "ui"
        / "app.py"
    ).read_text(encoding="utf-8")
    assert '("double11", "活动中心", "marketing")' in source
    assert '("double11", "双11活动", "marketing")' not in source


def test_limited_activity_applies_minimum_absolute_discount() -> None:
    class Client:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        @staticmethod
        def fetch_product_payload(_product_id, **_kwargs):
            return {
                "product": {
                    "skus": [
                        {"sku_id": "SKU-1", "sale_price": 2005, "stock_num": 10},
                        {"sku_id": "SKU-2", "sale_price": 1000, "stock_num": 10},
                    ]
                }
            }

        def create_limited_discount_task(self, payload):
            self.payloads.append(payload)
            return {"errcode": 0}

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "limited-price.db"))
        client = Client()
        service.client_for = lambda _store: client
        store = Store(
            id=1,
            name="限时活动价格店",
            app_id="wx-limited-price",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        errors = service.create_limited_discount_tasks(
            store,
            ["PRODUCT-1"],
            0.85,
            min_discount_cents=500,
        )

    assert errors == []
    assert [
        item["sale_price"]
        for item in client.payloads[0]["limited_discount_skus"]
    ] == [1505, 500]
    assert client.payloads[0]["end_time"] - client.payloads[0]["start_time"] == (
        7 * 86400
    )


def test_limited_activity_accepts_seven_day_duration() -> None:
    class Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class Settings:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def set_setting(self, key: str, value: str) -> None:
            self.values[key] = value

    class PageStub:
        limited_discount_var = Var("8.5")
        limited_min_discount_var = Var("1.00")
        limited_duration_var = Var("168")

        def __init__(self) -> None:
            self.db = Settings()

    page = PageStub()

    assert ActivityCenterPage._limited_settings(page) == (8.5, 100, 168)
    assert page.db.values == {
        "limited_activity_min_discount": "1.00",
        "limited_activity_duration_hours": "168",
    }


def test_limited_activity_rejects_duration_over_seven_days() -> None:
    class Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class Settings:
        @staticmethod
        def set_setting(_key: str, _value: str) -> None:
            raise AssertionError("invalid duration must not be persisted")

    class PageStub:
        limited_discount_var = Var("8.5")
        limited_min_discount_var = Var("1.00")
        limited_duration_var = Var("169")
        db = Settings()

    with pytest.raises(ValueError, match="1–168 小时"):
        ActivityCenterPage._limited_settings(PageStub())


def test_limited_service_accepts_seven_days_and_rejects_longer_ranges() -> None:
    class Client:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        @staticmethod
        def fetch_product_payload(_product_id, **_kwargs):
            return {
                "product": {
                    "skus": [
                        {"sku_id": "SKU-7D", "sale_price": 2005, "stock_num": 10}
                    ]
                }
            }

        def create_limited_discount_task(self, payload):
            self.payloads.append(payload)
            return {"errcode": 0}

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "limited-seven-day.db"))
        client = Client()
        service.client_for = lambda _store: client
        store = Store(
            id=1,
            name="七天限时活动店",
            app_id="wx-limited-seven-day",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        start = int(time.time()) + 300
        errors = service.create_limited_discount_tasks(
            store,
            ["PRODUCT-7D"],
            0.85,
            start_time=start,
            end_time=start + 7 * 86400,
        )

        assert errors == []
        assert client.payloads[0]["end_time"] - client.payloads[0]["start_time"] == (
            7 * 86400
        )
        with pytest.raises(ValueError, match="不能超过 7 天"):
            service.create_limited_discount_tasks(
                store,
                ["PRODUCT-7D"],
                0.85,
                start_time=start,
                end_time=start + 7 * 86400 + 1,
            )


@pytest.mark.parametrize(
    ("discount", "minimum", "message"),
    [
        ("8.6", "1.00", "1–8.5 折"),
        ("8.5", "0.99", "不能低于 1 元"),
    ],
)
def test_limited_activity_rejects_platform_invalid_values(
    discount: str,
    minimum: str,
    message: str,
) -> None:
    class Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class Settings:
        @staticmethod
        def set_setting(_key: str, _value: str) -> None:
            raise AssertionError("invalid settings must not be persisted")

    class PageStub:
        limited_discount_var = Var(discount)
        limited_min_discount_var = Var(minimum)
        limited_duration_var = Var("168")
        db = Settings()

    with pytest.raises(ValueError, match=message):
        ActivityCenterPage._limited_settings(PageStub())


def test_limited_preview_displays_platform_rule_popup_for_invalid_discount() -> None:
    class Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class PageStub:
        limited_discount_var = Var("8.6")
        limited_min_discount_var = Var("1.00")
        limited_duration_var = Var("168")
        db = object()
        _limited_settings = ActivityCenterPage._limited_settings

        @staticmethod
        def _require_stores():
            return [object()]

    page = PageStub()
    with patch(
        "weimi_manager.ui.activity_center.messagebox.showwarning"
    ) as showwarning:
        ActivityCenterPage._preview_limited_products(page)

    title, message = showwarning.call_args.args[:2]
    assert title == "限时活动参数"
    assert "1–8.5 折" in message


def test_limited_service_rejects_platform_invalid_values() -> None:
    class Client:
        @staticmethod
        def fetch_product_payload(_product_id, **_kwargs):
            return {
                "product": {
                    "skus": [
                        {"sku_id": "SKU-RULE", "sale_price": 2005, "stock_num": 10}
                    ]
                }
            }

        @staticmethod
        def create_limited_discount_task(_payload):
            raise AssertionError("invalid rules must not reach the platform")

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "limited-rule.db"))
        service.client_for = lambda _store: Client()
        store = Store(
            id=1,
            name="限时规则测试店",
            app_id="wx-limited-rule",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        now = int(time.time()) + 300
        with pytest.raises(ValueError, match="1–8.5 折"):
            service.create_limited_discount_tasks(
                store,
                ["PRODUCT-RULE"],
                0.86,
                start_time=now,
                end_time=now + 86400,
            )
        with pytest.raises(ValueError, match="不能低于 1 元"):
            service.create_limited_discount_tasks(
                store,
                ["PRODUCT-RULE"],
                0.85,
                start_time=now,
                end_time=now + 86400,
                min_discount_cents=99,
            )


def test_snapup_renewal_handles_multiple_activities_and_reports_completion() -> None:
    class Tree:
        def selection(self):
            return ("1:101", "1:102", "2:201")

    class Db:
        def __init__(self) -> None:
            self.records: list[tuple] = []

        def record_activity_operation(self, *args) -> None:
            self.records.append(args)

    class WebSession:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str, int]] = []

        def renew_snapup_activity(self, store, activity_id, start_time, days):
            self.calls.append((store.id, activity_id, days))
            return {"status": "created"}

    class Window:
        def __init__(self) -> None:
            self.web_session = WebSession()
            self.messages: list[str] = []
            self.task_args: list[dict] = []

        def set_status(self, message: str, **_kwargs) -> None:
            self.messages.append(message)

        def run_task(self, work, done, _error, **kwargs):
            assert "command" not in kwargs
            assert "store_app_ids" not in kwargs
            self.task_args.append(kwargs)
            done(work(None))
            return True

    class PageStub:
        snapup_tree = Tree()
        _snapup_rows = {
            "1:101": {
                "store_id": 1,
                "activity_id": "101",
                "title": "活动一",
                "endTime": int(time.time()) + 3600,
            },
            "1:102": {
                "store_id": 1,
                "activity_id": "102",
                "title": "活动二",
                "endTime": int(time.time()) + 7200,
            },
            "2:201": {
                "store_id": 2,
                "activity_id": "201",
                "title": "活动三",
                "endTime": int(time.time()) + 10800,
            },
        }
        _stores = {
            1: Store(
                id=1,
                name="店铺一",
                app_id="wx-store-one",
                group_name="",
                status="",
                last_sync_at=None,
                created_at="2026-11-13T00:00:00+00:00",
            ),
            2: Store(
                id=2,
                name="店铺二",
                app_id="wx-store-two",
                group_name="",
                status="",
                last_sync_at=None,
                created_at="2026-11-13T00:00:00+00:00",
            ),
        }

        def __init__(self) -> None:
            self.db = Db()
            self.window = Window()
            self.snapup_summary_var = type(
                "Summary",
                (),
                {"value": "", "set": lambda self, value: setattr(self, "value", value)},
            )()
            self.reloaded = False

        def _load_snapup_activities(self) -> None:
            self.reloaded = True

    page = PageStub()
    with (
        patch(
            "weimi_manager.ui.activity_center.messagebox.askyesno",
            return_value=True,
        ),
        patch(
            "weimi_manager.ui.activity_center.messagebox.showinfo"
        ) as showinfo,
    ):
        ActivityCenterPage._renew_selected_snapup(page)

    assert page.window.web_session.calls == [
        (1, "101", 7),
        (1, "102", 7),
        (2, "201", 7),
    ]
    assert len(page.db.records) == 3
    assert page.reloaded is True
    assert page.snapup_summary_var.value == "成功 3 场，失败 0 场"
    title, message = showinfo.call_args.args[:2]
    assert title == "所有任务已完成"
    assert "店铺一：已完成 2 场" in message
    assert "店铺二：已完成 1 场" in message


def test_snapup_selection_supports_select_all_and_clear() -> None:
    class Tree:
        def __init__(self) -> None:
            self.items = ("1:101", "1:102", "2:201")
            self.selected: tuple[str, ...] = ()

        def get_children(self):
            return self.items

        def selection(self):
            return self.selected

        def selection_set(self, *items) -> None:
            self.selected = tuple(items)

        def selection_remove(self, *items) -> None:
            removed = set(items)
            self.selected = tuple(item for item in self.selected if item not in removed)

    class PageStub:
        def __init__(self) -> None:
            self.snapup_tree = Tree()

    page = PageStub()
    ActivityCenterPage._select_all_snapup(page)
    assert page.snapup_tree.selected == page.snapup_tree.items
    ActivityCenterPage._clear_snapup_selection(page)
    assert page.snapup_tree.selected == ()


def test_snapup_refresh_reads_all_selected_stores() -> None:
    class Tree:
        def __init__(self) -> None:
            self.rows: list[tuple[str, tuple]] = []

        def delete(self, *_items) -> None:
            self.rows.clear()

        def get_children(self):
            return ()

        def insert(self, _parent, _index, *, iid, values):
            self.rows.append((iid, values))

    class Service:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def snapup_activities(self, store):
            self.calls.append(store.id)
            return [
                {
                    "activity_id": str(100 + store.id),
                    "title": f"{store.name}活动",
                    "status": 1,
                    "startTime": int(time.time()) - 60,
                    "endTime": int(time.time()) + 86400,
                    "product_count": store.id,
                }
            ]

    class Window:
        def __init__(self) -> None:
            self.service = Service()
            self.messages: list[str] = []
            self.task_args: list[dict] = []

        def set_status(self, message: str, **_kwargs) -> None:
            self.messages.append(message)

        def run_task(self, work, done, _error, **kwargs):
            assert "command" not in kwargs
            assert "store_app_ids" not in kwargs
            self.task_args.append(kwargs)
            done(work(None))
            return True

    stores = [
        Store(
            id=1,
            name="店铺一",
            app_id="wx-store-one",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        ),
        Store(
            id=2,
            name="店铺二",
            app_id="wx-store-two",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        ),
    ]
    stores[0].web_status = "已登录"
    stores[1].web_status = "已登录"

    class Summary:
        value = ""

        def set(self, value: str) -> None:
            self.value = value

    class PageStub:
        def __init__(self) -> None:
            self.window = Window()
            self.snapup_tree = Tree()
            self.snapup_summary_var = Summary()
            self._snapup_rows: dict[str, dict] = {}

        @staticmethod
        def _require_stores():
            return stores

    page = PageStub()
    ActivityCenterPage._load_snapup_activities(page)

    assert page.window.service.calls == [1, 2]
    assert len(page.snapup_tree.rows) == 2
    assert set(page._snapup_rows) == {"1:101", "2:102"}
    assert page.snapup_summary_var.value == "2 场限时抢购"


def test_private_snapup_create_and_delete_use_multi_product_endpoints() -> None:
    client = object.__new__(ShopPrivateClient)
    calls: list[tuple[str, dict]] = []

    def post(path: str, body: dict):
        calls.append((path, body))
        return {"code": 0}

    client._post = post
    client.create_snapup_activity(
        {
            "title": "澜琅限时7天",
            "snapupProducts": [{"productId": "P1"}],
            "startTime": 100,
            "endTime": 200,
        }
    )
    client.delete_snapup_activity("123")

    assert calls[0][0].endswith("/createSnapUpTaskV2")
    assert calls[0][1]["uploadSource"] == 6
    assert calls[0][1]["snapupProducts"] == [{"productId": "P1"}]
    assert calls[1] == (
        "/shop-faas/market/cgi/snapupV2/deleteSnapUpTaskV2",
        {"activityId": "123"},
    )


def test_private_snapup_product_lookup_batches_and_merges_products() -> None:
    client = object.__new__(ShopPrivateClient)
    calls: list[dict] = []

    def get(_path: str, params: dict):
        calls.append(params)
        return {
            "code": 0,
            "products": [
                {"productKey": {"productId": product_id}}
                for product_id in params["productIds"]
            ],
        }

    client._get = get
    product_ids = [str(index) for index in range(45)]
    products = client.fetch_snapup_products(product_ids, batch_size=20)

    assert [len(call["productIds"]) for call in calls] == [20, 20, 5]
    assert len(products) == 45


def test_service_creates_one_multi_product_activity_for_store() -> None:
    class Private:
        def __init__(self) -> None:
            self.payload = None

        @staticmethod
        def fetch_snapup_products(product_ids):
            products = [
                {
                    "productKey": {"productId": product_id},
                    "status": 5,
                    "productSkus": [
                        {
                            "skuKey": {"skuId": f"SKU-{product_id}"},
                            "status": 5,
                            "productSkuInfo": {
                                "salePrice": 2000,
                                "stockInfo": {"stockNum": "8"},
                            },
                        }
                    ],
                }
                for product_id in product_ids
            ]
            products.append(
                {
                    "productKey": {"productId": "P-DELETED"},
                    "status": 6,
                    "productSkus": [
                        {
                            "skuKey": {"skuId": "SKU-DELETED"},
                            "status": 5,
                            "productSkuInfo": {
                                "salePrice": 2000,
                                "stockInfo": {"stockNum": "8"},
                            },
                        }
                    ],
                }
            )
            return products

        def create_snapup_activity(self, payload):
            self.payload = payload
            return {"code": 0, "activityId": "12345"}

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "multi-product.db"))
        private = Private()
        store = Store(
            id=1,
            name="多商品活动店",
            app_id="wx-multi-product",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        start = int(time.time()) + 300
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=private,
        ):
            result = service.create_snapup_activity(
                store,
                ["P1", "P2", "P-DELETED"],
                0.85,
                start_time=start,
                end_time=start + 7 * 86400,
                min_discount_cents=100,
            )

    assert result["activity_id"] == "12345"
    assert result["product_count"] == 2
    assert result["skipped_count"] == 1
    assert len(private.payload["snapupProducts"]) == 2
    assert all(
        product["snapupSkus"][0]["snapupPrice"] == "1700"
        for product in private.payload["snapupProducts"]
    )
    assert all(
        product["snapupSkus"][0]["snapupStock"] == "0"
        for product in private.payload["snapupProducts"]
    )


def test_service_rejects_unsupported_snapup_stock_before_network_write() -> None:
    class Private:
        def __init__(self) -> None:
            self.created = False

        @staticmethod
        def fetch_snapup_products(_product_ids):
            return [
                {
                    "productKey": {"productId": "P1"},
                    "status": 5,
                    "productSkus": [
                        {
                            "skuKey": {"skuId": "SKU-P1"},
                            "status": 5,
                            "productSkuInfo": {
                                "salePrice": 2000,
                                "stockInfo": {"stockNum": "8"},
                            },
                        }
                    ],
                }
            ]

        def create_snapup_activity(self, _payload):
            self.created = True
            return {"code": 0}

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "snapup-stock.db"))
        private = Private()
        store = Store(
            id=1,
            name="库存参数测试店",
            app_id="wx-snapup-stock",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=private,
        ):
            with pytest.raises(ValueError, match="禁止设置抢购库存"):
                service.create_snapup_activity(
                    store,
                    ["P1"],
                    0.8,
                    stock_limit_per_sku=1,
                )

    assert private.created is False


def test_service_snapup_creation_truncates_to_platform_limit() -> None:
    class Private:
        def __init__(self) -> None:
            self.requested: list[str] = []
            self.payload = None

        def fetch_snapup_products(self, product_ids):
            self.requested = product_ids
            return [
                {
                    "productKey": {"productId": product_id},
                    "status": 5,
                    "productSkus": [
                        {
                            "skuKey": {"skuId": f"SKU-{product_id}"},
                            "status": 5,
                            "productSkuInfo": {
                                "salePrice": 2000,
                                "stockInfo": {"stockNum": "8"},
                            },
                        }
                    ],
                }
                for product_id in product_ids
            ]

        def create_snapup_activity(self, payload):
            self.payload = payload
            return {"code": 0, "activityId": "LIMIT-200"}

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "multi-product-limit.db"))
        private = Private()
        store = Store(
            id=1,
            name="商品上限测试店",
            app_id="wx-multi-limit",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        start = int(time.time()) + 300
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=private,
        ):
            result = service.create_snapup_activity(
                store,
                [f"P{index}" for index in range(205)],
                0.85,
                start_time=start,
                end_time=start + 7 * 86400,
            )

    assert len(private.requested) == 205
    assert len(private.payload["snapupProducts"]) == 200
    assert result["product_count"] == 200


def test_service_snapup_creation_respects_total_sku_limit() -> None:
    class Private:
        def __init__(self) -> None:
            self.payload = None

        @staticmethod
        def fetch_snapup_products(product_ids):
            return [
                {
                    "productKey": {"productId": product_id},
                    "status": 5,
                    "productSkus": [
                        {
                            "skuKey": {"skuId": f"SKU-{product_id}-{index}"},
                            "status": 5,
                            "productSkuInfo": {
                                "salePrice": 2000,
                                "stockInfo": {"stockNum": "8"},
                            },
                        }
                        for index in range(900)
                    ],
                }
                for product_id in product_ids
            ]

        def create_snapup_activity(self, payload):
            self.payload = payload
            return {"code": 0, "activityId": "SKU-LIMIT"}

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "sku-limit.db"))
        private = Private()
        store = Store(
            id=1,
            name="SKU 上限测试店",
            app_id="wx-sku-limit",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        start = int(time.time()) + 300
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=private,
        ):
            result = service.create_snapup_activity(
                store,
                ["P1", "P2", "P3"],
                0.85,
                start_time=start,
                end_time=start + 7 * 86400,
            )

    assert result["product_count"] == 2
    assert result["sku_count"] == 1800
    assert result["skipped_count"] == 1
    assert result["sku_limit_reached"] is True
    assert len(private.payload["snapupProducts"]) == 2


def test_service_creates_followup_activities_for_remaining_products() -> None:
    class Private:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        @staticmethod
        def fetch_snapup_products(product_ids):
            return [
                {
                    "productKey": {"productId": product_id},
                    "status": 5,
                    "productSkus": [
                        {
                            "skuKey": {"skuId": f"SKU-{product_id}-1"},
                            "status": 5,
                            "productSkuInfo": {
                                "salePrice": 2000,
                                "stockInfo": {"stockNum": "8"},
                            },
                        }
                    ],
                }
                for product_id in product_ids
            ]

        def create_snapup_activity(self, payload):
            self.payloads.append(payload)
            return {
                "code": 0,
                "activityId": f"ACT-{len(self.payloads)}",
            }

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "followup-activities.db"))
        private = Private()
        store = Store(
            id=1,
            name="多场活动测试店",
            app_id="wx-followup",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        start = int(time.time()) + 300
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=private,
        ):
            result = service.create_snapup_activity(
                store,
                [f"P{index}" for index in range(205)],
                0.85,
                start_time=start,
                end_time=start + 7 * 86400,
                create_all=True,
            )

    assert result["activity_count"] == 2
    assert result["product_count"] == 205
    assert result["sku_count"] == 205
    assert [len(item["snapupProducts"]) for item in private.payloads] == [
        200,
        5,
    ]


def test_service_skips_products_already_in_active_activity() -> None:
    class Private:
        def __init__(self) -> None:
            self.requested: list[str] = []
            self.payload = None

        @staticmethod
        def snapup_activities():
            return [
                {
                    "status": 0,
                    "extra": {
                        "snapupProducts": [
                            {"productId": "P1"},
                        ]
                    },
                }
            ]

        def fetch_snapup_products(self, product_ids):
            self.requested = list(product_ids)
            return [
                {
                    "productKey": {"productId": product_id},
                    "status": 5,
                    "productSkus": [
                        {
                            "skuKey": {"skuId": f"SKU-{product_id}"},
                            "status": 5,
                            "productSkuInfo": {
                                "salePrice": 2000,
                                "stockInfo": {"stockNum": "8"},
                            },
                        }
                    ],
                }
                for product_id in product_ids
            ]

        def create_snapup_activity(self, payload):
            self.payload = payload
            return {"code": 0, "activityId": "REMAINING"}

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "existing-activity.db"))
        private = Private()
        store = Store(
            id=1,
            name="续建活动店",
            app_id="wx-existing-activity",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        start = int(time.time()) + 300
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=private,
        ):
            result = service.create_snapup_activity(
                store,
                ["P1", "P2"],
                0.85,
                start_time=start,
                end_time=start + 7 * 86400,
                create_all=True,
            )

    assert private.requested == ["P2"]
    assert result["already_active_count"] == 1
    assert result["activity_count"] == 1
    assert private.payload["snapupProducts"][0]["productId"] == "P2"


def test_service_reports_no_followup_when_remaining_products_are_invalid() -> None:
    class Private:
        @staticmethod
        def snapup_activities():
            return [
                {
                    "status": 1,
                    "extra": {"snapupProducts": [{"productId": "P1"}]},
                }
            ]

        @staticmethod
        def fetch_snapup_products(_product_ids):
            return [
                {
                    "productKey": {"productId": "P-DELETED"},
                    "status": 6,
                    "productSkus": [],
                }
            ]

    with tempfile.TemporaryDirectory() as tmp:
        service = SyncService(Database(Path(tmp) / "no-followup.db"))
        store = Store(
            id=1,
            name="无后续活动店",
            app_id="wx-no-followup",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        )
        start = int(time.time()) + 300
        with patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=Private(),
        ):
            result = service.create_snapup_activity(
                store,
                ["P1", "P-DELETED"],
                0.85,
                start_time=start,
                end_time=start + 7 * 86400,
                create_all=True,
            )

    assert result["activity_count"] == 0
    assert result["already_active_count"] == 1
    assert result["invalid_skipped_count"] == 1


def test_limited_page_groups_selected_products_into_one_activity_per_store() -> None:
    class Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class Tree:
        @staticmethod
        def selection():
            return ("1:P1", "1:P2", "2:P3")

    class Db:
        def __init__(self) -> None:
            self.operations: list[tuple] = []

        @staticmethod
        def set_setting(_key: str, _value: str) -> None:
            return None

        def record_activity_operation(self, *args) -> None:
            self.operations.append(args)

    class Service:
        def __init__(self) -> None:
            self.calls: list[tuple[int, list[str]]] = []

        def create_snapup_activity(
            self,
            store,
            product_ids,
            _discount,
            **_kwargs,
        ):
            self.calls.append((store.id, list(product_ids)))
            return {
                "activity_id": f"ACT-{store.id}",
                "activity_count": 1,
                "product_count": len(product_ids),
                "skipped_count": 0,
                "invalid_skipped_count": 0,
                "capacity_skipped_count": 0,
                "failed_count": 0,
                "truncated": False,
            }

    class Summary:
        value = ""

        def set(self, value: str) -> None:
            self.value = value

    class Window:
        def __init__(self) -> None:
            self.service = Service()

        @staticmethod
        def run_task(work, done, _error, **_kwargs):
            done(work(None))
            return True

        @staticmethod
        def set_status(*_args, **_kwargs) -> None:
            return None

    stores = [
        Store(
            id=1,
            name="店铺一",
            app_id="wx-one",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        ),
        Store(
            id=2,
            name="店铺二",
            app_id="wx-two",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        ),
    ]
    product_rows = {
        "1:P1": {"store_id": 1, "product_id": "P1"},
        "1:P2": {"store_id": 1, "product_id": "P2"},
        "2:P3": {"store_id": 2, "product_id": "P3"},
    }

    class PageStub:
        limited_discount_var = Var("8.5")
        limited_min_discount_var = Var("1.00")
        limited_duration_var = Var("168")
        limited_product_tree = Tree()
        _limited_product_rows = product_rows
        limited_activity_summary_var = Summary()
        _limited_settings = ActivityCenterPage._limited_settings

        def __init__(self) -> None:
            self.db = Db()
            self.window = Window()

        @staticmethod
        def _require_stores():
            return stores

    page = PageStub()
    with (
        patch(
            "weimi_manager.ui.activity_center.messagebox.askyesno",
            return_value=True,
        ),
        patch("weimi_manager.ui.activity_center.messagebox.showinfo"),
    ):
        ActivityCenterPage._create_limited_activity(page, individual=True)

    assert page.window.service.calls == [
        (1, ["P1", "P2"]),
        (2, ["P3"]),
    ]
    assert len(page.db.operations) == 2
    assert (
        page.limited_activity_summary_var.value
        == "限时活动创建完成：成功 2 家，失败 0 家"
    )


def test_limited_preview_renders_only_selected_page_size() -> None:
    class Tree:
        def __init__(self) -> None:
            self.rows: list[tuple[str, tuple]] = []

        def get_children(self):
            return tuple(iid for iid, _values in self.rows)

        def delete(self, *_items) -> None:
            self.rows.clear()

        def insert(self, _parent, _index, *, iid, values):
            self.rows.append((iid, values))

    class Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def set(self, value: str) -> None:
            self.value = value

    class Button:
        def __init__(self) -> None:
            self.state = ""

        def configure(self, **kwargs) -> None:
            self.state = kwargs.get("state", self.state)

    class PageStub:
        limited_product_tree = Tree()
        limited_page_size_var = Var("100")
        limited_page_label_var = Var("")
        limited_activity_summary_var = Var("")
        limited_prev_button = Button()
        limited_next_button = Button()
        _limited_preview_rows: list[dict] = []
        _limited_preview_errors: list[str] = []
        _limited_product_rows: dict[str, dict] = {}
        _limited_preview_page = 1
        _limited_page_size = ActivityCenterPage._limited_page_size
        _limited_page_count = ActivityCenterPage._limited_page_count
        _render_limited_preview_page = (
            ActivityCenterPage._render_limited_preview_page
        )
        _update_limited_page_controls = (
            ActivityCenterPage._update_limited_page_controls
        )
        _change_limited_page = ActivityCenterPage._change_limited_page

    page = PageStub()
    ActivityCenterPage._after_limited_preview(
        page,
        {
            "rows": [
                {
                    "store_id": 1,
                    "store_name": "店铺",
                    "product_id": str(index),
                    "title": f"商品 {index}",
                    "price": 1000,
                    "stock": 10,
                    "status": "在售",
                }
                for index in range(250)
            ],
            "errors": [],
        },
    )

    assert len(page.limited_product_tree.rows) == 100
    assert page.limited_page_label_var.value == "第 1 / 3 页"
    assert "1–100" in page.limited_activity_summary_var.value
    ActivityCenterPage._change_limited_page(page, 1)
    assert len(page.limited_product_tree.rows) == 100
    assert page.limited_page_label_var.value == "第 2 / 3 页"
    assert page.limited_product_tree.rows[0][0].endswith(":100")


def test_snapup_delete_selected_uses_service_and_refreshes() -> None:
    class Tree:
        @staticmethod
        def selection():
            return ("1:101", "1:102", "2:201")

    class Db:
        def __init__(self) -> None:
            self.records: list[tuple] = []

        def record_activity_operation(self, *args) -> None:
            self.records.append(args)

    class Service:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str]] = []

        def delete_snapup_activity(self, store, activity_id):
            self.calls.append((store.id, activity_id))
            return {"code": 0}

    class Summary:
        value = ""

        def set(self, value: str) -> None:
            self.value = value

    class Window:
        def __init__(self) -> None:
            self.service = Service()

        @staticmethod
        def run_task(work, done, _error, **_kwargs):
            done(work(None))
            return True

        @staticmethod
        def set_status(*_args, **_kwargs) -> None:
            return None

    stores = {
        1: Store(
            id=1,
            name="店铺一",
            app_id="wx-one",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        ),
        2: Store(
            id=2,
            name="店铺二",
            app_id="wx-two",
            group_name="",
            status="",
            last_sync_at=None,
            created_at="2026-11-13T00:00:00+00:00",
        ),
    }

    class PageStub:
        snapup_tree = Tree()
        _stores = stores
        _snapup_rows = {
            "1:101": {
                "store_id": 1,
                "store_name": "店铺一",
                "activity_id": "101",
                "title": "活动一",
            },
            "1:102": {
                "store_id": 1,
                "store_name": "店铺一",
                "activity_id": "102",
                "title": "活动二",
            },
            "2:201": {
                "store_id": 2,
                "store_name": "店铺二",
                "activity_id": "201",
                "title": "活动三",
            },
        }

        def __init__(self) -> None:
            self.db = Db()
            self.window = Window()
            self.snapup_summary_var = Summary()
            self.reloaded = False

        def _load_snapup_activities(self) -> None:
            self.reloaded = True

    page = PageStub()
    with (
        patch(
            "weimi_manager.ui.activity_center.messagebox.askyesno",
            return_value=True,
        ),
        patch("weimi_manager.ui.activity_center.messagebox.showinfo"),
    ):
        ActivityCenterPage._delete_selected_snapup(page)

    assert page.window.service.calls == [
        (1, "101"),
        (1, "102"),
        (2, "201"),
    ]
    assert len(page.db.records) == 3
    assert page.reloaded is True
    assert page.snapup_summary_var.value == "删除活动：成功 3 场，失败 0 场"
