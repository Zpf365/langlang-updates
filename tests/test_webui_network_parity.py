from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
import base64
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Product, SyncResult
from weimi_manager.service import SyncService
from weimi_manager.webui.controller import NextUiController


class FakeNetworkService:
    def __init__(self) -> None:
        self.fund_queries = []
        self.storefront_reads = []
        self.storefront_actions = []
        self.order_syncs = []
        self.platform_violation_syncs = []
        self.stock_updates = []
        self.product_writes = []
        self.product_detail_reads = []
        self.product_detail_override = {}
        self.taglinks = []
        self.shipping_refreshes = []
        self.shipment_creates = []
        self.shipment_completes = []
        self.after_sale_uploads = []
        self.coupon_syncs = []
        self.coupon_writes = []

    def fetch_funds_summary(self, store, start_time, end_time):
        self.fund_queries.append((store.id, start_time, end_time))
        return {
            "available_amount": 12345,
            "pending_amount": 678,
            "settle_amount": 900,
            "estimated_amount": 1200,
            "flow_list": [
                {
                    "flow_id": "flow-1",
                    "type": "settle",
                    "amount": 500,
                    "create_time": "2026-11-22 10:00:00",
                    "status": "success",
                }
            ],
        }

    def sync_storefront(self, store):
        self.storefront_reads.append(store.id)
        return [
            {
                "product_id": "product-1",
                "title": "真实主页商品",
                "status_text": "上架",
                "raw": {"must_not_escape": True},
            }
        ]

    def storefront_action(self, store, action, product_id, value):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, f"storefront_{action}")
        self.storefront_actions.append(
            (store.id, action, product_id, value)
        )

    def sync_orders(self, store, start_time, end_time):
        self.order_syncs.append((store.id, start_time, end_time))
        return SyncResult(
            "orders",
            3,
            "订单同步完成",
            success=3,
        )

    def sync_platform_violation_blacklist(self, store):
        self.platform_violation_syncs.append(store.id)
        database = getattr(self, "database")
        database.set_setting(
            "violation_blacklist",
            json.dumps(
                [
                    {
                        "product_id": "blocked-1",
                        "title": "禁止重复上传的商品标题",
                        "reason": "平台同步：出售假冒商品",
                    }
                ],
                ensure_ascii=False,
            ),
        )
        return SyncResult(
            "platform_violation_blacklist",
            1,
            "平台违规同步完成",
            success=1,
        )

    def fetch_product_taglink(self, store, product_id):
        self.taglinks.append((store.id, product_id))
        return f"https://example.invalid/product/{product_id}"

    def product_write(self, store, action, payload=None, product_id="", **_kwargs):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "product_write")
            authorizer(store.app_id, "product_delete")
            authorizer(store.app_id, "product_web_batch_action")
        self.product_writes.append((store.id, action, product_id))
        return {"errcode": 0}

    def fetch_product_detail(self, store, product_id):
        self.product_detail_reads.append((store.id, product_id))
        row = self.database.get_product(store.id, product_id)
        if row is None:
            raise ValueError("商品不存在")
        raw = json.loads(str(row.get("raw_json") or "{}"))
        values = {
            "store_id": store.id,
            "store_name": store.name,
            "product_id": product_id,
            "title": str(row.get("title") or ""),
            "status": int(row.get("status") or 0),
            "status_text": str(row.get("status_text") or ""),
            "edit_status": int(row.get("edit_status") or 0),
            "min_price": int(row.get("min_price") or 0),
            "sku_count": int(row.get("sku_count") or 0),
            "stock_total": int(row.get("stock_total") or 0),
            "spu_code": str(row.get("spu_code") or ""),
            "out_product_id": str(row.get("out_product_id") or ""),
            "head_img": str(row.get("head_img") or ""),
            "raw": raw,
        }
        values.update(self.product_detail_override)
        product = Product(**values)
        self.database.upsert_product(product)
        return product

    def update_product_stock(
        self,
        store,
        product_id,
        sku_id,
        diff_type,
        amount,
    ):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "product_stock_update")
        self.stock_updates.append(
            (store.id, product_id, sku_id, diff_type, amount)
        )
        return None

    def refresh_shipping_options(self, store):
        self.shipping_refreshes.append(store.id)
        return SyncResult(
            "shipping_options",
            1,
            "电子面单配置已刷新",
            success=1,
        )

    def create_shipment(self, store, order_id):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "ewaybill_create")
        self.shipment_creates.append((store.id, order_id))
        return {
            "order_id": order_id,
            "waybill_id": "SF-TEST-1",
            "status": "created",
        }

    def complete_shipment(self, store, order_id):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "order_delivery_send")
        self.shipment_completes.append((store.id, order_id))
        return {
            "order_id": order_id,
            "waybill_id": "SF-TEST-1",
            "status": "shipped",
        }

    def upload_after_sale_media(self, store, paths):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "aftersale_media_upload")
        self.after_sale_uploads.append(
            (store.id, [Path(path).name for path in paths])
        )
        return [
            f"media-{index}"
            for index, _path in enumerate(paths, start=1)
        ]

    def sync_coupons(self, store):
        self.coupon_syncs.append(store.id)
        return SyncResult("coupons", 1, "优惠券同步完成", success=1)

    def coupon_write(self, store, action, payload):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, f"coupon_{action}")
        self.coupon_writes.append((store.id, action, dict(payload)))
        return {"errcode": 0}


class FakeWebSession:
    def __init__(self) -> None:
        self.customer_started = threading.Event()
        self.customer_release = threading.Event()
        self.customer_stores = []
        self.cookie_imports = []

    def open_official_customer_console(self, store):
        self.customer_stores.append(store.id)
        self.customer_started.set()
        self.customer_release.wait(timeout=2)

    def import_cookie_header(self, store, header):
        self.cookie_imports.append((store.id, header))
        return {
            "status": "已登录",
            "detail": "Cookie 已验证",
            "cookies": [{"name": "sensitive"}],
        }


def _controller(tmp_path: Path, *, web_session=None):
    database = Database(tmp_path / "webui-network.db")
    store_id = database.add_store(
        "网络测试店铺",
        "wx-network-test",
        "secret",
    )
    service = FakeNetworkService()
    service.database = database
    controller = NextUiController(
        database,
        product_service=service,
        web_session=web_session,
        auth_context=SimpleNamespace(
            account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
            username="zpfly",
            member_role="owner",
            permissions={},
            offline=False,
        ),
    )
    return controller, database, service, store_id


def test_finance_summary_uses_real_service_and_persists_audit(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)

    payload = controller.dispatch(
        "finance.summary",
        {
            "store_ids": [store_id],
            "start_date": "2026-11-01",
            "end_date": "2026-11-22",
        },
    )

    assert service.fund_queries
    assert payload["succeeded"] == 1
    assert payload["rows"][0]["available_amount"] == 12345
    assert payload["rows"][0]["flow_list"][0]["flow_id"] == "flow-1"
    logs = database.list_task_logs(scope="finance")
    assert logs
    assert logs[0]["status"] == "success"


def test_storefront_read_and_write_use_old_service_and_do_not_leak_raw(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)

    loaded = controller.dispatch(
        "storefront.load",
        {"store_id": store_id},
    )
    result = controller.dispatch(
        "storefront.action",
        {
            "store_id": store_id,
            "product_id": "product-1",
            "action": "hide",
            "value": 1,
        },
    )

    assert loaded["products"] == [
        {
            "product_id": "product-1",
            "title": "真实主页商品",
            "status_text": "上架",
        }
    ]
    assert result["status"] == "success"
    assert service.storefront_actions == [
        (store_id, "hide", "product-1", 1)
    ]
    logs = database.list_task_logs(scope="storefront")
    assert any(row["status"] == "success" for row in logs)


def test_storefront_partial_detail_failure_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)
    service.sync_storefront = lambda _store: [
        {
            "product_id": "product-ok",
            "title": "正常商品",
            "status_text": "上架",
        },
        {
            "product_id": "product-failed",
            "title": "(详情读取失败)",
            "status_text": "—",
            "raw": None,
            "_detail_error": "接口超时",
        },
    ]

    result = controller.dispatch(
        "storefront.load",
        {"store_id": store_id},
    )

    assert result["status"] == "partial"
    assert result["errors"] == ["product-failed: 接口超时"]
    log = database.list_task_logs(scope="storefront")[0]
    assert log["status"] == "partial"
    assert "详情失败 1 个" in log["message"]


def test_automation_creates_and_runs_real_order_sync_task(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)
    created = controller.dispatch(
        "automation.tasks.create",
        {
            "name": "订单测试同步",
            "task_type": "order_sync",
            "store_id": store_id,
            "interval_minutes": 1,
        },
    )
    task_id = created["task_id"]

    started = controller.dispatch(
        "automation.tasks.run-start",
        {"task_ids": [task_id]},
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = controller.dispatch(
            "automation.tasks.run-status",
            {"job_id": started["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.03)

    assert status["status"] == "completed"
    assert status["succeeded"] == 1
    assert service.order_syncs
    task = next(
        row
        for row in database.list_automation_tasks()
        if row["id"] == task_id
    )
    assert task["last_status"] == "成功"
    assert database.list_automation_runs()


def test_automation_scheduler_runs_due_tasks_and_reschedules(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)
    created = controller.dispatch(
        "automation.tasks.create",
        {
            "name": "到期订单同步",
            "task_type": "order_sync",
            "store_id": store_id,
            "interval_minutes": 5,
        },
    )
    task_id = int(created["task_id"])
    due_at = datetime.now() - timedelta(minutes=1)
    database.update_automation_task(
        task_id,
        next_run=due_at.strftime("%Y-%m-%d %H:%M:%S"),
    )

    started = controller.run_due_automation_tasks()

    assert started["due"] == 1
    assert started["started"] is True
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = controller.dispatch(
            "automation.tasks.run-status",
            {"job_id": started["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.03)
    assert status["status"] == "completed"
    assert service.order_syncs
    task = next(
        row
        for row in database.list_automation_tasks()
        if int(row["id"]) == task_id
    )
    assert task["last_status"] == "成功"
    assert task["next_run"] > due_at.strftime("%Y-%m-%d %H:%M:%S")


def test_automation_create_and_interval_update_control_next_run(
    tmp_path: Path,
) -> None:
    controller, database, _, store_id = _controller(tmp_path)
    created = controller.dispatch(
        "automation.tasks.create",
        {
            "name": "暂停创建的同步任务",
            "task_type": "order_sync",
            "store_id": store_id,
            "interval_minutes": 60,
            "enabled": False,
        },
    )
    task_id = int(created["task_id"])
    task = next(
        row
        for row in database.list_automation_tasks()
        if int(row["id"]) == task_id
    )
    assert task["enabled"] == 0

    updated = controller.dispatch(
        "automation.tasks.update",
        {
            "task_id": task_id,
            "interval_minutes": 5,
            "enabled": True,
        },
    )

    assert updated["task"]["enabled"] == 1
    assert updated["task"]["next_run"] > datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_hosted_schedule_runs_once_per_day_at_configured_time(
    tmp_path: Path,
) -> None:
    controller, database, _, store_id = _controller(tmp_path)
    database.update_hosted_config(store_id, enabled=1)
    now = datetime(2026, 11, 22, 9, 30, 0)
    database.set_setting("hosted_schedule_enabled", "1")
    database.set_setting("hosted_schedule_time", "09:30")
    database.set_setting("hosted_schedule_stores", json.dumps([store_id]))
    calls = []

    def fake_execute(payload):
        calls.append(payload)
        return {"executed": 1, "succeeded": 1, "errors": []}

    controller._execute_hosted = fake_execute

    first = controller.run_due_hosted_schedule(now)
    second = controller.run_due_hosted_schedule(now)

    assert first["started"] is True
    assert second["started"] is False
    assert calls == [{"store_ids": [store_id]}]
    assert database.get_setting("hosted_last_run_date", "") == "2026-11-22"


def test_platform_violation_sync_updates_upload_title_rule_library(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)
    database.update_web_status(store_id, "已登录", "")

    result = controller.dispatch(
        "violations.sync-platform-blacklist",
        {"store_ids": [store_id]},
    )

    assert result["succeeded"] == 1
    assert service.platform_violation_syncs == [store_id]
    titles = json.loads(
        database.get_setting("upload_violation_titles", "[]")
    )
    assert any(
        row.get("title") == "禁止重复上传的商品标题"
        for row in titles
    )


def test_platform_violation_sync_rejects_logged_out_store(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)
    database.update_web_status(store_id, "未登录", "登录失效")

    with pytest.raises(ValueError, match="已登录的真实店铺"):
        controller.dispatch(
            "violations.sync-platform-blacklist",
            {"store_ids": [store_id]},
        )

    assert service.platform_violation_syncs == []


def test_customer_console_rpc_does_not_block_workbench(
    tmp_path: Path,
) -> None:
    web_session = FakeWebSession()
    controller, database, _, store_id = _controller(
        tmp_path,
        web_session=web_session,
    )

    started = time.monotonic()
    result = controller.dispatch(
        "customer-service.open",
        {"store_id": store_id},
    )
    elapsed = time.monotonic() - started

    assert result["opening"] is True
    assert elapsed < 0.5
    assert web_session.customer_started.wait(timeout=1)
    web_session.customer_release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        logs = database.list_task_logs(scope="customer_service")
        if any(row["status"] == "success" for row in logs):
            break
        time.sleep(0.03)
    assert any(row["status"] == "success" for row in logs)


def test_product_stock_taglink_and_official_backend_are_wired(
    tmp_path: Path,
) -> None:
    web_session = FakeWebSession()

    class FakeProductWebSession(FakeWebSession):
        def open_official_product(self, store, product_id):
            self.customer_stores.append(store.id)
            self.customer_started.set()

    web_session = FakeProductWebSession()
    controller, database, service, store_id = _controller(
        tmp_path,
        web_session=web_session,
    )
    database.upsert_product(
        type(
            "ProductRow",
            (),
            {
                "store_id": store_id,
                "product_id": "product-1",
                "title": "库存商品",
                "head_img": "",
                "status": 5,
                "status_text": "上架",
                "edit_status": 0,
                "min_price": 1000,
                "sku_count": 1,
                "stock_total": 10,
                "spu_code": "",
                "out_product_id": "",
                "raw": {
                    "skus": [
                        {
                            "sku_id": "sku-1",
                            "sku_name": "默认规格",
                            "sale_price": 1000,
                            "stock_num": 10,
                        }
                    ]
                },
            },
        )()
    )

    detail = controller.dispatch(
        "products.detail",
        {"store_id": store_id, "product_id": "product-1"},
    )
    taglink = controller.dispatch(
        "products.taglink",
        {"store_id": store_id, "product_id": "product-1"},
    )
    stock = controller.dispatch(
        "products.update-stock",
        {
            "store_id": store_id,
            "product_id": "product-1",
            "sku_id": "sku-1",
            "diff_type": 1,
            "amount": 2,
        },
    )
    opening = controller.dispatch(
        "products.open-official",
        {"store_id": store_id, "product_id": "product-1"},
    )

    assert detail["product"]["skus"][0]["sku_id"] == "sku-1"
    assert taglink["link"].endswith("/product-1")
    assert stock["amount"] == 2
    assert service.taglinks == [(store_id, "product-1")]
    assert service.product_detail_reads == [(store_id, "product-1")]
    assert service.stock_updates == [
        (store_id, "product-1", "sku-1", 1, 2)
    ]
    assert opening["opening"] is True
    assert web_session.customer_started.wait(timeout=1)


def test_product_detail_reads_official_product_and_refreshes_stale_cache(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)
    database.upsert_product(
        Product(
            store_id=store_id,
            store_name="网络测试店铺",
            product_id="product-fresh",
            title="本地旧标题",
            status=5,
            status_text="上架",
            edit_status=0,
            min_price=900,
            sku_count=1,
            stock_total=3,
            spu_code="",
            out_product_id="",
            head_img="",
            raw={
                "skus": [
                    {
                        "sku_id": "sku-old",
                        "stock_num": 3,
                    }
                ]
            },
        )
    )
    service.product_detail_override = {
        "title": "官方实时标题",
        "stock_total": 8,
        "raw": {
            "skus": [
                {
                    "sku_id": "sku-fresh",
                    "sku_name": "官方实时规格",
                    "stock_num": 8,
                }
            ]
        },
    }

    result = controller.dispatch(
        "products.detail",
        {"store_id": store_id, "product_id": "product-fresh"},
    )

    assert service.product_detail_reads == [(store_id, "product-fresh")]
    assert result["product"]["title"] == "官方实时标题"
    assert result["product"]["skus"][0]["sku_id"] == "sku-fresh"
    cached = database.get_product(store_id, "product-fresh")
    assert cached is not None
    assert cached["title"] == "官方实时标题"
    assert json.loads(cached["raw_json"])["skus"][0]["stock_num"] == 8


def test_order_ewaybill_actions_use_real_service_chain(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)

    refreshed = controller.dispatch(
        "orders.workbench.shipping-options",
        {"store_id": store_id},
    )
    created = controller.dispatch(
        "orders.workbench.shipment-create",
        {"store_id": store_id, "order_id": "order-1"},
    )
    completed = controller.dispatch(
        "orders.workbench.shipment-complete",
        {"store_id": store_id, "order_id": "order-1"},
    )

    assert refreshed["message"] == "电子面单配置已刷新"
    assert created["shipment"]["waybill_id"] == "SF-TEST-1"
    assert completed["shipment"]["status"] == "shipped"
    assert service.shipping_refreshes == [store_id]
    assert service.shipment_creates == [(store_id, "order-1")]
    assert service.shipment_completes == [(store_id, "order-1")]
    assert database.list_task_logs(scope="orders")


def test_successful_order_price_and_stock_writes_survive_refresh_failure(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui-write-refresh.db")
    store_id = database.add_store("写后刷新店铺", "wx-write-refresh", "secret")
    store = database.get_store(store_id)
    assert store is not None

    class Client:
        def __init__(self) -> None:
            self.price_writes = []
            self.stock_writes = []

        def update_order_price(self, payload):
            self.price_writes.append(payload)
            return {"errcode": 0}

        def fetch_order(self, _store, _order_id):
            raise RuntimeError("回查订单失败")

        def update_product_stock(
            self,
            product_id,
            sku_id,
            diff_type,
            amount,
        ):
            self.stock_writes.append(
                (product_id, sku_id, diff_type, amount)
            )
            return {"errcode": 0}

        def fetch_product(self, _store, _product_id):
            raise RuntimeError("回查商品失败")

    client = Client()
    service = SyncService(database)
    service.platform_write_authorizer = lambda _app_id, _action: None
    service.client_for = lambda _store: client

    service.update_order_price(
        store,
        "order-refresh-fail",
        [
            {
                "product_id": "product-1",
                "sku_id": "sku-1",
                "change_price": 1880,
            }
        ],
        False,
        0,
    )
    product = service.update_product_stock(
        store,
        "product-1",
        "sku-1",
        1,
        2,
    )

    assert client.price_writes
    assert client.stock_writes == [("product-1", "sku-1", 1, 2)]
    assert product is None


def test_after_sale_proof_upload_accepts_browser_base64_payload(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)

    result = controller.dispatch(
        "orders.after-sales.upload-media",
        {
            "store_id": store_id,
            "after_sale_id": "after-sale-1",
            "files": [
                {
                    "name": "proof.png",
                    "content_base64": base64.b64encode(b"fake-image").decode(),
                }
            ],
        },
    )

    assert result["media_ids"] == ["media-1"]
    assert service.after_sale_uploads
    assert any(
        name.endswith("proof.png")
        for name in service.after_sale_uploads[0][1]
    )
    assert database.list_task_logs(scope="orders")


def test_coupon_sync_and_status_use_existing_activity_service(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)

    synced = controller.dispatch(
        "activity.coupons.sync",
        {"store_id": store_id},
    )
    written = controller.dispatch(
        "activity.coupons.status",
        {
            "store_id": store_id,
            "coupon_id": "coupon-1",
            "status": 4,
        },
    )

    assert synced["result"]["message"] == "优惠券同步完成"
    assert written["coupon_id"] == "coupon-1"
    assert service.coupon_syncs == [store_id]
    assert service.coupon_writes == [
        (
            store_id,
            "status",
            {"coupon_id": "coupon-1", "status": 4},
        )
    ]
    assert database.list_task_logs(scope="activities")


def test_coupon_create_uses_real_service_and_persists_audit(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)

    result = controller.dispatch(
        "activity.coupons.create",
        {
            "store_id": store_id,
            "coupon": {
                "type": 104,
                "name": "测试直减券",
                "promote_info": {"promote_type": 1},
                "discount_info": {
                    "discount_fee": 500,
                    "discount_num": 0,
                },
                "receive_info": {
                    "total_num": 100,
                    "limit_num_one_person": 1,
                    "start_time": 1_800_000_000,
                    "end_time": 1_800_086_400,
                },
                "valid_info": {
                    "valid_type": 2,
                    "valid_day_num": 7,
                },
            },
        },
    )

    assert result["result"]["errcode"] == 0
    assert service.coupon_writes == [
        (
            store_id,
            "create",
            {
                "type": 104,
                "name": "测试直减券",
                "promote_info": {"promote_type": 1},
                "discount_info": {
                    "discount_fee": 500,
                    "discount_num": 0,
                },
                "receive_info": {
                    "total_num": 100,
                    "limit_num_one_person": 1,
                    "start_time": 1_800_000_000,
                    "end_time": 1_800_086_400,
                },
                "valid_info": {
                    "valid_type": 2,
                    "valid_day_num": 7,
                },
            },
        )
    ]
    assert any(
        row["operation"] == "新建优惠券"
        for row in database.list_task_logs(scope="activities")
    )


def test_activity_renew_create_and_delete_failures_are_persisted(
    tmp_path: Path,
) -> None:
    class FailingActivityService(FakeNetworkService):
        def create_snapup_activity(self, store, product_ids, discount, **_kwargs):
            raise RuntimeError("抢购创建失败")

        def delete_snapup_activity(self, store, activity_id):
            raise RuntimeError("抢购删除失败")

    class FailingActivitySession(FakeWebSession):
        def renew_coupon(self, *_args, **_kwargs):
            raise RuntimeError("优惠券续期失败")

        def renew_snapup_activity(self, *_args, **_kwargs):
            raise RuntimeError("抢购续期失败")

    database = Database(tmp_path / "webui-activity-failures.db")
    store_id = database.add_store("活动失败店铺", "wx-activity-fail", "secret")
    service = FailingActivityService()
    service.database = database
    controller = NextUiController(
        database,
        product_service=service,
        web_session=FailingActivitySession(),
    )

    for action, payload in (
        (
            "activity.coupons.renew",
            {
                "store_id": store_id,
                "coupon_id": "coupon-1",
                "coupon_type": 1,
                "promote_type": 1,
                "days": 7,
            },
        ),
        (
            "activity.snapup.renew",
            {
                "store_id": store_id,
                "activity_id": "snapup-1",
                "days": 7,
            },
        ),
        (
            "activity.snapup.create",
            {
                "store_id": store_id,
                "product_ids": ["product-1"],
                "discount": 0.9,
            },
        ),
        (
            "activity.snapup.delete",
            {
                "store_id": store_id,
                "activity_id": "snapup-1",
            },
        ),
    ):
        with pytest.raises(RuntimeError):
            controller.dispatch(action, payload)

    failed = [
        row
        for row in database.list_task_logs(scope="activities")
        if row["status"] == "failed"
    ]
    assert {row["operation"] for row in failed} >= {
        "续期优惠券",
        "续期限时抢购",
        "创建限时抢购",
        "删除限时抢购",
    }


def test_activity_prefill_drafts_are_persistent_and_filtered(
    tmp_path: Path,
) -> None:
    controller, database, _, store_id = _controller(tmp_path)
    database.set_setting(
        "violation_blacklist",
        json.dumps([{"product_id": "blocked-product"}]),
    )

    saved = controller.dispatch(
        "activity.double11.drafts.save",
        {
            "store_id": store_id,
            "drafts": [
                {
                    "product_id": "product-1",
                    "title": "预填商品",
                    "activity_price": 1880,
                    "activity_stock": 8,
                },
                {
                    "product_id": "product-1",
                    "title": "重复商品",
                    "activity_price": 1700,
                    "activity_stock": 9,
                },
                {
                    "product_id": "blocked-product",
                    "title": "违规商品",
                    "activity_price": 100,
                    "activity_stock": 1,
                },
            ],
        },
    )
    loaded = controller.dispatch(
        "activity.double11.drafts.list",
        {"store_id": store_id},
    )

    assert saved["count"] == 1
    assert loaded["drafts"] == [
        {
            "product_id": "product-1",
            "title": "预填商品",
            "activity_price": 1880,
            "activity_stock": 8,
        }
    ]
    assert database.list_task_logs(scope="activities")


def test_every_frontend_call_has_registered_controller_action() -> None:
    root = Path(__file__).resolve().parents[1]
    frontend_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            *(root / "webui" / "src" / "pages").glob("*.vue"),
            *(root / "webui" / "src" / "components").glob("*.vue"),
        ]
    )
    frontend_actions = set(
        re.findall(r'call\("([^"]+)"', frontend_text)
    )
    controller_text = (
        root / "weimi_manager" / "webui" / "controller.py"
    ).read_text(encoding="utf-8")
    registered_actions = set(
        re.findall(
            r'"([^"]+)": self\._[A-Za-z0-9_]+',
            controller_text,
        )
    )

    assert frontend_actions
    assert frontend_actions <= registered_actions


def test_cookie_import_returns_no_cookie_payload_and_audits(
    tmp_path: Path,
) -> None:
    web_session = FakeWebSession()
    controller, database, _, store_id = _controller(
        tmp_path,
        web_session=web_session,
    )

    result = controller.dispatch(
        "stores.import-cookie",
        {
            "store_id": store_id,
            "cookie_header": "session=secret-value",
        },
    )

    assert web_session.cookie_imports == [
        (store_id, "session=secret-value")
    ]
    assert "cookies" not in result["result"]
    assert "secret-value" not in json.dumps(result, ensure_ascii=False)
    logs = database.list_task_logs(scope="stores")
    assert logs
    assert "secret-value" not in json.dumps(logs, ensure_ascii=False)


def test_private_store_delete_authorizes_web_batch_delete_path(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui-private-delete.db")
    store_id = database.add_store(
        "仅网页登录店铺",
        "wx-cookie-only",
        "",
    )

    class PrivateDeleteService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.calls = []

        def product_write(self, store, action, payload=None, product_id="", **_kwargs):
            assert action == "delete"
            for write_action in (
                "product_write",
                "product_delete",
                "product_web_batch_action",
            ):
                self.platform_write_authorizer(store.app_id, write_action)
            self.calls.append((store.id, action, product_id))
            return {"success": True}

    service = PrivateDeleteService()
    controller = NextUiController(database, product_service=service)

    result = controller.dispatch(
        "products.delete",
        {"store_id": store_id, "product_id": "product-private"},
    )

    assert result == {"success": True}
    assert service.calls == [(store_id, "delete", "product-private")]


def test_blacklisted_product_delete_is_blocked_before_network_write(
    tmp_path: Path,
) -> None:
    controller, database, service, store_id = _controller(tmp_path)
    database.set_setting(
        "violation_blacklist",
        json.dumps(
            [
                {
                    "product_id": "blocked-product",
                    "title": "违规商品",
                    "reason": "平台违规",
                }
            ],
            ensure_ascii=False,
        ),
    )

    with pytest.raises(ValueError, match="违规黑名单"):
        controller.dispatch(
            "products.delete",
            {
                "store_id": store_id,
                "product_id": "blocked-product",
            },
        )

    assert service.product_writes == []
