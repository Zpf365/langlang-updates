from __future__ import annotations

from pathlib import Path
import base64
from datetime import datetime
import json
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem, Product, SyncResult
from weimi_manager import __version__
from weimi_manager.service import ProductDeleteUncertainError
from weimi_manager.webui.controller import (
    NextUiController,
    _ScopedUploadProductService,
)


PRIMARY_NAV_LABELS = [
    "店铺管理",
    "订单管理",
    "中转订单",
    "物流监控",
    "利润统计",
    "商品管理",
    "上传商品",
    "货源市场",
    "托管中心",
    "违规中心",
    "采购账号",
    "AI客服",
]


def _internal_auth_context() -> SimpleNamespace:
    return SimpleNamespace(
        account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
        username="zpfly",
        email="",
        offline=False,
        organization_name="",
        member_role="owner",
        permissions={},
        entitlements={},
        license=None,
        tokens=SimpleNamespace(access_token="test-token"),
    )


def test_bootstrap_matches_reference_navigation_and_isolates_dongxiao(tmp_path: Path) -> None:
    controller = NextUiController(
        Database(tmp_path / "webui.db"),
        auth_context=_internal_auth_context(),
    )

    payload = controller.dispatch("bootstrap", {})

    assert [item["label"] for item in payload["primary_nav"]] == PRIMARY_NAV_LABELS
    assert payload["brand"] == {"name": "澜琅", "version": __version__}
    assert "sales_related" not in {
        item["key"]
        for item in [*payload["primary_nav"], *payload["more_nav"]]
    }
    assert all(item["hidden"] is True for item in payload["isolated_modules"])


def test_store_crud_is_shared_by_web_ui_and_database(tmp_path: Path) -> None:
    controller = NextUiController(
        Database(tmp_path / "webui.db"),
        auth_context=_internal_auth_context(),
    )

    created = controller.dispatch(
        "stores.create",
        {
            "name": "测试店铺",
            "app_id": "wx-webui-test",
            "secret": "temporary-secret",
            "group_name": "测试分组",
        },
    )
    store_id = created["store"]["id"]

    updated = controller.dispatch(
        "stores.update",
        {
            "store_id": store_id,
            "name": "测试店铺-已修改",
            "group_name": "新分组",
        },
    )
    assert updated["store"]["name"] == "测试店铺-已修改"
    assert "secret" not in updated["store"]

    listed = controller.dispatch("stores.list", {"search": "已修改"})
    assert [item["id"] for item in listed["stores"]] == [store_id]

    controller.db.set_setting(
        f"store_metrics_{store_id}",
        json.dumps({"dsr": 4.68}),
    )
    controller.db.set_setting(
        f"webui_store_score_state_{store_id}",
        json.dumps({"values": {"dsr": {"value": 4.68}}}),
    )
    controller.dispatch("stores.delete", {"store_id": store_id})
    assert controller.dispatch("stores.list", {})["stores"] == []
    assert controller.db.get_setting(f"store_metrics_{store_id}", "") == ""
    assert (
        controller.db.get_setting(
            f"webui_store_score_state_{store_id}",
            "",
        )
        == ""
    )


def test_store_secret_can_be_revealed_like_the_reference_ui(tmp_path: Path) -> None:
    controller = NextUiController(
        Database(tmp_path / "webui.db"),
        auth_context=_internal_auth_context(),
    )
    store_id = controller.dispatch(
        "stores.create",
        {
            "name": "密钥店铺",
            "app_id": "wx-reveal-secret",
            "secret": "test-secret-value",
            "group_name": "默认",
        },
    )["store"]["id"]

    revealed = controller.dispatch("stores.reveal-secret", {"store_id": store_id})

    assert revealed == {"store_id": store_id, "secret": "test-secret-value"}


def test_store_groups_can_be_created_assigned_and_deleted(tmp_path: Path) -> None:
    controller = NextUiController(
        Database(tmp_path / "webui.db"),
        auth_context=_internal_auth_context(),
    )
    store_id = controller.dispatch(
        "stores.create",
        {
            "name": "分组店铺",
            "app_id": "wx-store-group",
            "secret": "test-secret-value",
            "group_name": "",
        },
    )["store"]["id"]

    created = controller.dispatch(
        "stores.groups.create",
        {"group_name": "田亚辉"},
    )
    assigned = controller.dispatch(
        "stores.assign-group",
        {"store_id": store_id, "group_name": "田亚辉"},
    )
    filtered = controller.dispatch("stores.list", {})["stores"]
    deleted = controller.dispatch(
        "stores.groups.delete",
        {"group_name": "田亚辉"},
    )

    assert created["groups"] == ["田亚辉"]
    assert assigned["groups"] == ["田亚辉"]
    assert filtered[0]["group_name"] == "田亚辉"
    assert deleted["groups"] == []


def test_store_batch_group_assignment_updates_all_selected_stores_at_once(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(
        database,
        auth_context=_internal_auth_context(),
    )
    store_ids = [
        controller.dispatch(
            "stores.create",
            {
                "name": f"批量分组店铺{index}",
                "app_id": f"wx-batch-group-{index}",
                "secret": "secret",
                "group_name": "",
            },
        )["store"]["id"]
        for index in range(3)
    ]

    result = controller.dispatch(
        "stores.batch-assign-group",
        {"store_ids": store_ids, "group_name": "重点店铺"},
    )

    assert result["updated"] == 3
    assert result["group_name"] == "重点店铺"
    assert {
        store.group_name
        for store in database.list_stores()
        if store.id in store_ids
    } == {"重点店铺"}
    logs = database.list_task_logs(limit=20, scope="stores")
    assert any(
        "批量调整店铺分组" in str(log.get("operation") or "")
        and list(store_ids) == list(
            json.loads(log.get("detail") or "{}").get("store_ids", [])
        )
        for log in logs
    )


def test_store_batch_delete_is_atomic_and_removes_local_cache_and_data(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_id = database.add_store("批量删除一", "wx-batch-delete-1", "secret")
    second_id = database.add_store("批量删除二", "wx-batch-delete-2", "secret")
    keep_id = database.add_store("保留店铺", "wx-batch-keep", "secret")
    database.upsert_order(
        Order(
            store_id=first_id,
            store_name="批量删除一",
            order_id="delete-order",
            status=20,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_000,
            pay_time=1_700_000_000,
            total_amount=100,
            product_amount=100,
            freight_amount=0,
            discount_amount=0,
            buyer_name="",
            phone="",
            address="",
            waybill_id="",
            carrier="",
            merchant_notes="",
        )
    )
    session = FakeWebSession()
    session.data_dir = tmp_path / "data"
    first_profile = session.data_dir / "edge_profiles" / f"store_{first_id}"
    second_profile = session.data_dir / "edge_profiles" / f"store_{second_id}"
    first_profile.mkdir(parents=True)
    second_profile.mkdir(parents=True)
    (first_profile / "profile.txt").write_text("cache", encoding="utf-8")
    (second_profile / "profile.txt").write_text("cache", encoding="utf-8")
    for store_id in (first_id, second_id):
        database.set_setting(
            f"store_metrics_{store_id}",
            json.dumps({"dsr": 4.68}),
        )
        database.set_setting(
            f"webui_store_score_state_{store_id}",
            json.dumps({"values": {"dsr": {"value": 4.68}}}),
        )
        database.set_setting(
            f"webui_previous_store_metrics_{store_id}",
            json.dumps({"dsr": 4.60}),
        )
    controller = NextUiController(database, web_session=session)

    result = controller.dispatch(
        "stores.batch-delete",
        {"store_ids": [first_id, second_id]},
    )

    assert result["deleted"] == 2
    assert result["official_store_deleted"] is False
    assert {store.id for store in database.list_stores()} == {keep_id}
    assert database.list_orders(store_id=first_id) == []
    assert not first_profile.exists()
    assert not second_profile.exists()
    for store_id in (first_id, second_id):
        assert database.get_setting(f"store_metrics_{store_id}", "") == ""
        assert (
            database.get_setting(
                f"webui_store_score_state_{store_id}",
                "",
            )
            == ""
        )
        assert (
            database.get_setting(
                f"webui_previous_store_metrics_{store_id}",
                "",
            )
            == ""
        )
    logs = database.list_task_logs(limit=20, scope="stores")
    deleted_logs = [
        log
        for log in logs
        if str(log.get("operation") or "") == "批量删除店铺"
    ]
    assert {
        json.loads(log["detail"])["store_id"]
        for log in deleted_logs
    } == {
        first_id,
        second_id,
    }

    with pytest.raises(ValueError, match="店铺不存在"):
        controller.dispatch(
            "stores.batch-delete",
            {"store_ids": [keep_id, 999999]},
        )
    assert database.get_store(keep_id) is not None

    with pytest.raises(ValueError, match="不能重复"):
        controller.dispatch(
            "stores.batch-delete",
            {"store_ids": [keep_id, keep_id]},
        )
    assert database.get_store(keep_id) is not None

    with pytest.raises(ValueError, match="至少选择"):
        controller.dispatch("stores.batch-delete", {"store_ids": []})
    with pytest.raises(ValueError, match="不能超过 500"):
        controller.dispatch(
            "stores.batch-delete",
            {"store_ids": list(range(1, 502))},
        )
    assert database.get_store(keep_id) is not None


def test_unknown_action_is_rejected(tmp_path: Path) -> None:
    controller = NextUiController(Database(tmp_path / "webui.db"))

    try:
        controller.dispatch("not-a-real-action", {})
    except ValueError as exc:
        assert "不支持" in str(exc)
    else:
        raise AssertionError("unknown actions must be rejected")


def test_orders_page_uses_cached_rows_without_exposing_raw_payload(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("订单店铺", "wx-webui-orders", "secret")
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="订单店铺",
            order_id="order-webui-1",
            status=20,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_010,
            pay_time=1_700_000_005,
            total_amount=1990,
            product_amount=1990,
            freight_amount=0,
            discount_amount=0,
            buyer_name="测试买家",
            phone="13800000000",
            address="测试地址",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="product-image-1",
                    sku_id="sku-image-1",
                    title="测试商品",
                    sku_count=1,
                    sale_price=1990,
                    thumb_url="https://example.invalid/product.jpg",
                )
            ],
            raw={"private": "must-not-leak"},
        )
    )

    payload = NextUiController(database).dispatch("orders.list", {})

    assert payload["orders"][0]["order_id"] == "order-webui-1"
    assert payload["orders"][0]["status_text"] == "待发货"
    assert payload["orders"][0]["item_product_id"] == "product-image-1"
    assert payload["orders"][0]["item_thumb_url"] == "https://example.invalid/product.jpg"
    assert "raw_json" not in payload["orders"][0]
    assert "raw" not in payload["orders"][0]
    assert payload["total"] == 1
    assert payload["returned"] == 1
    assert payload["truncated"] is False

    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="订单店铺",
            order_id="order-webui-2",
            status=20,
            status_text="待发货",
            create_time=1_700_000_020,
            update_time=1_700_000_030,
            pay_time=1_700_000_025,
            total_amount=990,
            product_amount=990,
            freight_amount=0,
            discount_amount=0,
            buyer_name="第二位买家",
            phone="13900000000",
            address="测试地址二",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[],
            raw={},
        )
    )
    limited = NextUiController(database).dispatch("orders.list", {"limit": 1})
    assert limited["total"] == 2
    assert limited["returned"] == 1
    assert limited["truncated"] is True


def test_orders_page_uses_platform_status_over_stale_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("已发货店铺", "wx-shipped-order", "secret")
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="已发货店铺",
            order_id="order-shipped-status",
            status=30,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_010,
            pay_time=1_700_000_005,
            total_amount=1000,
            product_amount=1000,
            freight_amount=0,
            discount_amount=0,
            buyer_name="买家",
            phone="",
            address="",
            waybill_id="YT123456",
            carrier="圆通",
            merchant_notes="",
            items=[],
            raw={},
        )
    )

    row = NextUiController(database).dispatch(
        "orders.list",
        {},
    )["orders"][0]

    assert row["status"] == 30
    assert row["status_text"] == "待收货"


def test_hosted_configuration_is_saved_through_web_interface(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("托管店铺", "wx-webui-hosted", "secret")
    controller = NextUiController(database)

    controller.dispatch(
        "hosted.save-config",
        {
            "store_id": store_id,
            "enabled": True,
            "revoke_audit": True,
            "league_commission_percent": 18,
            "listed_product_count": 120,
        },
    )
    payload = controller.dispatch("hosted.list", {})

    row = payload["stores"][0]
    assert row["config"]["enabled"] == 1
    assert row["config"]["revoke_audit"] == 1
    assert row["config"]["league_commission_percent"] == 18


class FakeWebSession:
    def __init__(self) -> None:
        self.probed: list[int] = []
        self.opened: list[int] = []
        self.logged_in: list[int] = []
        self.login_scopes: list[set[int] | None] = []
        self.cleanup_calls = 0

    def probe(self, store):
        self.probed.append(store.id)
        return "已登录", "测试"

    def open_official_backend(self, store):
        self.opened.append(store.id)

    def open_official_order(self, store, order_id):
        self.opened.append((store.id, order_id))

    def interactive_login(self, store, *, allowed_store_ids=None, **_kwargs):
        self.logged_in.append(store.id)
        self.login_scopes.append(allowed_store_ids)
        return {"success": True, "matched_store_ids": [store.id]}

    def cleanup_cache(self):
        self.cleanup_calls += 1
        return 12


def test_store_web_actions_use_existing_browser_session(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("网页店铺", "wx-web-session", "secret")
    web_session = FakeWebSession()
    controller = NextUiController(database, web_session=web_session)

    assert controller.dispatch("stores.probe", {"store_id": store_id})["status"] == "已登录"
    controller.dispatch("stores.open-backend", {"store_id": store_id})
    login = controller.dispatch("stores.login", {"store_id": store_id})
    cleanup = controller.dispatch("stores.clear-cache", {})

    assert web_session.probed == [store_id]
    assert web_session.opened == [store_id]
    assert web_session.logged_in == [store_id]
    assert web_session.login_scopes == [{store_id}]
    assert login["success"] is True
    assert cleanup["removed"] == 12


def test_store_login_batch_opens_one_browser_for_all_selected_stores(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_ids = [
        database.add_store(
            f"店铺{index}",
            f"wx-login-{index}",
            "secret",
        )
        for index in range(12)
    ]

    class BatchSession(FakeWebSession):
        def interactive_login(self, store, *, allowed_store_ids=None, **_kwargs):
            self.logged_in.append(store.id)
            self.login_scopes.append(allowed_store_ids)
            return {
                "success": True,
                "login_confirmed": True,
                "matched_store_ids": list(store_ids),
                "matched_store_names": [
                    f"店铺{index}" for index in range(12)
                ],
            }

    session = BatchSession()
    controller = NextUiController(database, web_session=session)
    result = controller.dispatch(
        "stores.login",
        {"store_ids": store_ids},
    )

    assert session.logged_in == [store_ids[0]]
    assert session.login_scopes == [set(store_ids)]
    assert result["requested_store_ids"] == store_ids
    assert result["requested_logged_in_count"] == 12
    assert result["saved_store_count"] == 12
    assert result["not_logged_in_store_ids"] == []


def test_store_login_batch_rejects_duplicate_click_without_second_browser(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_id = database.add_store("店铺一", "wx-login-first", "secret")
    second_id = database.add_store("店铺二", "wx-login-second", "secret")

    class BlockingSession(FakeWebSession):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def interactive_login(self, store, *, allowed_store_ids=None, **_kwargs):
            self.logged_in.append(store.id)
            self.login_scopes.append(allowed_store_ids)
            self.started.set()
            self.release.wait(timeout=3)
            return {
                "success": True,
                "matched_store_ids": [first_id, second_id],
            }

    session = BlockingSession()
    controller = NextUiController(database, web_session=session)
    result_holder: dict[str, dict] = {}
    worker = threading.Thread(
        target=lambda: result_holder.setdefault(
            "first",
            controller.dispatch(
                "stores.login",
                {"store_ids": [first_id, second_id]},
            ),
        )
    )
    worker.start()
    assert session.started.wait(timeout=2)

    duplicate = controller.dispatch(
        "stores.login.batch",
        {"store_ids": [first_id, second_id]},
    )
    assert duplicate["busy"] is True
    assert session.logged_in == [first_id]

    session.release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert result_holder["first"]["saved_store_count"] == 2
    assert session.logged_in == [first_id]


def test_store_login_batch_reports_missing_local_store_once(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    existing_id = database.add_store("现有店铺", "wx-login-existing", "secret")
    missing_id = existing_id + 500

    class ExistingOnlySession(FakeWebSession):
        def interactive_login(self, store, *, allowed_store_ids=None, **_kwargs):
            self.logged_in.append(store.id)
            self.login_scopes.append(allowed_store_ids)
            return {
                "success": True,
                "matched_store_ids": [existing_id],
            }

    session = ExistingOnlySession()
    controller = NextUiController(database, web_session=session)
    result = controller.dispatch(
        "stores.login",
        {"store_ids": [existing_id, missing_id]},
    )

    assert session.logged_in == [existing_id]
    assert session.login_scopes == [{existing_id}]
    assert result["missing_store_ids"] == [missing_id]
    assert result["not_logged_in_store_ids"] == [missing_id]
    assert result["saved_store_count"] == 1


def test_store_login_reports_when_scan_saved_other_shops_only(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    selected_id = database.add_store("所选店铺", "wx-login-selected", "secret")
    other_id = database.add_store("其他店铺", "wx-login-other", "secret")

    class OtherShopSession(FakeWebSession):
        def interactive_login(self, store, *, allowed_store_ids=None, **_kwargs):
            self.logged_in.append(store.id)
            self.login_scopes.append(allowed_store_ids)
            return {
                "login_confirmed": True,
                "matched_store_ids": [other_id],
                "matched_store_names": ["其他店铺"],
            }

    controller = NextUiController(
        database,
        web_session=OtherShopSession(),
    )
    result = controller.dispatch(
        "stores.login",
        {"store_id": selected_id},
    )

    assert result["requested_store_id"] == selected_id
    assert result["requested_store_logged_in"] is False
    assert result["matched_store_ids"] == [other_id]
    assert result["saved_store_count"] == 1


def test_official_order_browser_session_does_not_hold_workbench_rpc(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("官方订单店铺", "wx-official-order", "secret")

    class BlockingWebSession(FakeWebSession):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def open_official_order(self, store, order_id):
            self.started.set()
            self.release.wait(timeout=2)
            self.opened.append((store.id, order_id))

    web_session = BlockingWebSession()
    controller = NextUiController(database, web_session=web_session)

    started_at = time.monotonic()
    result = controller.dispatch(
        "orders.workbench.open-official",
        {"store_id": store_id, "order_id": "official-order-1"},
    )
    elapsed = time.monotonic() - started_at

    assert result["opening"] is True
    assert result["order_id"] == "official-order-1"
    assert result["job_id"]
    assert elapsed < 0.5
    assert web_session.started.wait(timeout=0.5)
    web_session.release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = controller.dispatch(
            "orders.workbench.open-official.status",
            {"job_id": result["job_id"]},
        )
        if status["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert status["status"] == "completed"


class FakeProductService:
    def __init__(self) -> None:
        self.writes = []
        self.listings = []
        self.order_syncs = []
        self.order_notes = []
        self.decrypted_orders = []
        self.violation_syncs = []
        self.procurement_logins = []
        self.procurement_syncs = []
        self.product_refreshes = []
        self.store_refreshes = []
        self.aftersale_actions = []

    def product_write(self, store, action, payload=None, product_id=""):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "product_write")
            authorizer(
                store.app_id,
                {
                    "delete": "product_delete",
                    "cancel_audit": "product_audit_cancel",
                }.get(action, ""),
            )
        self.writes.append((store.id, action, product_id, payload))
        return {"errcode": 0}

    def set_products_listing(self, store, product_ids, listed):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "product_listing")
            authorizer(
                store.app_id,
                "product_listing" if listed else "product_delisting",
            )
        self.listings.append((store.id, tuple(product_ids), listed))
        return SyncResult(
            "product_listing",
            len(product_ids),
            "批量商品状态已更新",
            success=len(product_ids),
        )

    def sync_orders(self, store, start_time, end_time):
        self.order_syncs.append((store.id, start_time, end_time))
        return {"count": 3}

    def update_order_notes(self, store, order_id, notes, color=0):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "order_notes_update")
        self.order_notes.append((store.id, order_id, notes, color))
        return {"errcode": 0}

    def decode_order_sensitive(self, store, order_id):
        self.decrypted_orders.append((store.id, order_id))
        return {"errcode": 0}

    def sync_violation_library(self, store):
        self.violation_syncs.append(store.id)
        return {"errcode": 0}

    def procurement_login(self, account_id):
        self.procurement_logins.append(account_id)
        return {"success": True}

    def sync_procurement_account(self, account_id):
        self.procurement_syncs.append(account_id)
        return SyncResult("procurement", 3, "采购数据已同步", success=3)

    def procurement_browse(self, account_id):
        self.procurement_logins.append(account_id)
        return SyncResult("procurement_browse", 2, "采购网页已保存", success=2)

    def match_procurement_orders(self, store):
        return 1

    def sync_procurement_note_to_wechat(self, store, order_id):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "order_notes_update")
        return SyncResult("procurement_note_sync", 1, "采购备注已同步", success=1)

    def request_order_address_change(self, store, order_id, address):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "order_address_update")
        return None

    def update_order_price(
        self,
        store,
        order_id,
        change_infos,
        change_express,
        express_fee,
    ):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "order_price_update")
        return None

    def update_order_delivery_packages(
        self,
        store,
        order_id,
        packages,
        product_infos,
    ):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, "order_delivery_update")
        return None

    def aftersale_action(self, store, after_sale_id, action, data=None):
        authorizer = getattr(self, "platform_write_authorizer", None)
        if callable(authorizer):
            authorizer(store.app_id, f"aftersale_{action}")
        self.aftersale_actions.append((store.id, after_sale_id, action, data))
        return SyncResult(f"aftersale_{action}", 1, "售后操作已提交", success=1)

    def sync_after_sales(
        self,
        store,
        start_time,
        end_time,
        progress=None,
        time_field="create",
    ):
        return SyncResult("after_sales", 1, "售后同步完成", success=1)

    def refresh_product_tab(self, store, tab_key, cancel_event=None, progress=None, **_kwargs):
        self.product_refreshes.append((store.id, tab_key))
        return {"store_ids": [store.id], "errors": []}

    def refresh_store_metrics(self, store):
        self.store_refreshes.append(store.id)
        return dict(store=None)


def test_product_actions_are_available_to_web_ui(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("商品店铺", "wx-product-actions", "secret")
    service = FakeProductService()
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=_internal_auth_context(),
    )

    controller.dispatch(
        "products.delete",
        {"store_id": store_id, "product_id": "product-1"},
    )
    listing_result = controller.dispatch(
        "products.set-listing",
        {
            "store_id": store_id,
            "product_ids": ["product-1", "product-2"],
            "listed": True,
        },
    )
    controller.dispatch(
        "products.cancel-audit",
        {"store_id": store_id, "product_id": "product-2"},
    )

    assert service.writes == [
        (store_id, "delete", "product-1", None),
        (store_id, "cancel_audit", "product-2", None),
    ]
    assert service.listings == [(store_id, ("product-1", "product-2"), True)]
    assert listing_result["resource"] == "product_listing"
    assert listing_result["success"] == 2


def test_product_delete_and_listing_operations_are_persistently_logged(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("商品日志店铺", "wx-product-logs", "secret")

    class LoggedProductService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None

        def product_write(self, store, action, payload=None, product_id="", **_kwargs):
            self.platform_write_authorizer(store.app_id, "product_write")
            self.platform_write_authorizer(
                store.app_id,
                {
                    "delete": "product_delete",
                    "cancel_audit": "product_audit_cancel",
                }.get(action, ""),
            )
            if product_id == "delete-fail":
                raise RuntimeError("远端删除失败")
            if product_id == "delete-pending":
                raise ProductDeleteUncertainError("回收站复核超时")
            return {"errcode": 0}

        def set_products_listing(self, store, product_ids, listed):
            self.platform_write_authorizer(store.app_id, "product_listing")
            return SyncResult(
                "product_listing",
                len(product_ids),
                "部分失败",
                success=max(0, len(product_ids) - 1),
                failed=1 if "listing-fail" in product_ids else 0,
                errors=(
                    ["listing-fail: 远端下架失败"]
                    if "listing-fail" in product_ids
                    else []
                ),
            )

    controller = NextUiController(
        database,
        product_service=LoggedProductService(),
    )

    controller.dispatch(
        "products.delete",
        {"store_id": store_id, "product_id": "delete-ok"},
    )
    with pytest.raises(RuntimeError, match="远端删除失败"):
        controller.dispatch(
            "products.delete",
            {"store_id": store_id, "product_id": "delete-fail"},
        )
    with pytest.raises(RuntimeError, match="复核超时"):
        controller.dispatch(
            "products.delete",
            {"store_id": store_id, "product_id": "delete-pending"},
        )
    controller.dispatch(
        "products.set-listing",
        {
            "store_id": store_id,
            "product_ids": ["listing-ok", "listing-fail"],
            "listed": False,
        },
    )
    controller.dispatch(
        "products.cancel-audit",
        {"store_id": store_id, "product_id": "cancel-ok"},
    )

    logs = controller.dispatch(
        "products.operation-logs",
        {"store_id": store_id},
    )["logs"]

    by_product = {row["product_id"]: row for row in logs}
    assert by_product["delete-ok"]["status"] == "success"
    assert by_product["delete-fail"]["status"] == "failed"
    assert "远端删除失败" in by_product["delete-fail"]["error"]
    assert by_product["delete-pending"]["status"] == "pending"
    assert "复核超时" in by_product["delete-pending"]["error"]
    assert by_product["listing-ok"]["status"] == "success"
    assert by_product["listing-fail"]["status"] == "failed"
    assert "远端下架失败" in by_product["listing-fail"]["error"]
    assert by_product["cancel-ok"]["status"] == "success"
    assert by_product["cancel-ok"]["action"] == "撤回审核"
    assert all(row["store_name"] == "商品日志店铺" for row in logs)


def test_order_write_actions_persist_success_and_failure_logs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui-order-write-logs.db")
    store_id = database.add_store("订单写日志店铺", "wx-order-write-logs", "secret")
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="订单写日志店铺",
            order_id="order-write-log-1",
            status=20,
            status_text="待发货",
            create_time=1_700_000_000,
            update_time=1_700_000_010,
            pay_time=1_700_000_005,
            total_amount=1990,
            product_amount=1990,
            freight_amount=0,
            discount_amount=0,
            buyer_name="测试买家",
            phone="13800000000",
            address="测试地址",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="product-1",
                    sku_id="sku-1",
                    title="测试商品",
                    sku_count=1,
                    sale_price=1990,
                    thumb_url="",
                )
            ],
            raw={},
        )
    )
    service = FakeProductService()
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=_internal_auth_context(),
    )

    controller.dispatch(
        "orders.workbench.address-update",
        {
            "store_id": store_id,
            "order_id": "order-write-log-1",
            "address": {
                "user_name": "测试买家",
                "tel_number": "13800000000",
                "province_name": "广东省",
                "city_name": "揭阳市",
                "county_name": "普宁市",
                "street_name": "",
                "detail_info": "测试地址",
            },
        },
    )
    controller.dispatch(
        "orders.workbench.delivery-update",
        {
            "store_id": store_id,
            "order_id": "order-write-log-1",
            "packages": [{"delivery_id": "SF", "waybill_id": "SF123"}],
        },
    )
    controller.dispatch(
        "orders.workbench.price-update",
        {
            "store_id": store_id,
            "order_id": "order-write-log-1",
            "changes": [
                {
                    "product_id": "product-1",
                    "sku_id": "sku-1",
                    "change_price": 1880,
                }
            ],
        },
    )
    controller.dispatch(
        "orders.after-sales.action",
        {
            "store_id": store_id,
            "after_sale_id": "after-sale-1",
            "operation": "accept",
            "data": {"media_files": ["media-proof-1"]},
        },
    )

    logs = database.list_task_logs(scope="orders")
    successful = {
        row["operation"]
        for row in logs
        if row["status"] == "success"
    }
    assert {
        "提交改址申请",
        "提交订单发货",
        "修改订单价格",
        "售后操作",
    } <= successful
    assert service.aftersale_actions == [
        (
            store_id,
            "after-sale-1",
            "accept",
            {"media_files": ["media-proof-1"]},
        )
    ]


def test_activity_webui_uses_old_catalog_preview_and_write_workflow(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("活动店铺", "wx-activity", "secret")

    class ActivityService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.calls = []

        def platform_activity_catalog(self, store):
            return {
                "source_store_id": store.id,
                "source_store_name": store.name,
                "activities": [
                    {
                        "activity_id": "1323",
                        "name": "平台满减",
                        "activity_state": 1,
                        "apply_state": 1,
                    }
                ],
            }

        def activity_preview(self, store, activity_id, **kwargs):
            self.calls.append(("preview", activity_id, kwargs))
            return {
                "store_id": store.id,
                "store_name": store.name,
                "valid_sku_count": 1,
                "rows": [
                    {
                        "productId": "P1",
                        "skuId": "S1",
                        "currentPrice": 1000,
                        "activityPrice": 900,
                        "status": "可报名",
                    }
                ],
            }

        def apply_platform_activity(self, store, activity_id):
            self.platform_write_authorizer(
                store.app_id,
                "platform_activity_apply",
            )
            return {"activity_id": activity_id}

        def activity_submit_items(self, store, activity_id, items):
            self.platform_write_authorizer(
                store.app_id,
                "platform_activity_items_submit",
            )
            return {"activity_id": activity_id, "count": len(items)}

        def double11_remove_all(self, store, activity_id):
            self.platform_write_authorizer(
                store.app_id,
                "platform_activity_items_remove",
            )
            return {"activity_id": activity_id, "removed": 3}

    database.record_activity_operation(
        "活动申报",
        "成功",
        store_id,
        "活动店铺",
        1323,
        1,
        "测试活动操作",
    )
    controller = NextUiController(
        database,
        product_service=ActivityService(),
    )

    catalog = controller.dispatch(
        "activity.catalog",
        {"store_id": store_id},
    )
    preview = controller.dispatch(
        "activity.preview",
        {"store_id": store_id, "activity_id": 1323},
    )
    controller.dispatch(
        "activity.apply",
        {"store_id": store_id, "activity_id": 1323},
    )
    submitted = controller.dispatch(
        "activity.submit-items",
        {
            "store_id": store_id,
            "activity_id": 1323,
            "items": preview["rows"],
        },
    )
    removed = controller.dispatch(
        "activity.remove-all",
        {"store_id": store_id, "activity_id": 1323},
    )
    operations = controller.dispatch(
        "activity.operations",
        {"store_id": store_id},
    )["operations"]

    assert catalog["activities"][0]["activity_id"] == "1323"
    assert preview["valid_sku_count"] == 1
    assert submitted["count"] == 1
    assert removed["removed"] == 3
    assert operations[0]["message"] == "测试活动操作"


def test_scoped_upload_product_service_authorizes_image_upload_and_listing(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("上传授权店铺", "wx-upload-scope", "secret")

    class UploadService:
        def product_write(self, store, action, payload, **_kwargs):
            authorizer = getattr(self, "platform_write_authorizer", None)
            for write_action in (
                "product_write",
                "product_add",
                "product_image_upload",
                "product_listing",
                "product_delisting",
            ):
                authorizer(store.app_id, write_action)
            return {"errcode": 0, "product_id": "product-uploaded"}

    service = UploadService()
    controller = NextUiController(database, product_service=service)
    scoped = _ScopedUploadProductService(controller, service)
    store = database.get_store(store_id)

    result = scoped.product_write(store, "add", {})

    assert result["product_id"] == "product-uploaded"


def test_product_list_exposes_real_sales_count_without_raw_payload(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("销量店铺", "wx-product-sales", "secret")
    database.list_products = lambda **_kwargs: [
        {
            "store_id": store_id,
            "product_id": "product-sales",
            "title": "真实销量商品",
            "status": 5,
            "status_text": "销售中",
            "min_price": 1990,
            "sku_count": 1,
            "stock_total": 8,
            "head_img": "https://example.invalid/product.jpg",
            "raw_json": json.dumps({"total_sold_num": 27}),
        }
    ]
    database.get_product_metrics_map = lambda _store_id: {
        "product-sales": {
            "views30": 456,
            "sold30": 23,
            "recommend_views30": 345,
            "updated_at": "2026-11-22 11:00:00",
        }
    }
    controller = NextUiController(database)

    product = controller.dispatch(
        "products.list",
        {"store_id": store_id},
    )["products"][0]

    assert product["sales_count"] == 27
    assert product["total_sales"] == 27
    assert product["sold30"] == 23
    assert product["views30"] == 456
    assert product["recommend_views30"] == 345
    assert product["review_count"] is None
    assert product["metrics_updated_at"] == "2026-11-22 11:00:00"
    assert "raw_json" not in product


def test_product_list_requires_store_or_explicit_all_stores(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_a = database.add_store("商品店铺 A", "wx-product-a", "secret")
    store_b = database.add_store("商品店铺 B", "wx-product-b", "secret")
    calls: list[int | None] = []

    def list_products(**kwargs):
        calls.append(kwargs["store_id"])
        return []

    database.list_products = list_products
    controller = NextUiController(database)

    with pytest.raises(ValueError, match="store_id"):
        controller.dispatch("products.list", {})

    controller.dispatch("products.list", {"store_id": store_a})
    controller.dispatch("products.list", {"store_id": store_b})
    controller.dispatch("products.list", {"store_id": "__all__"})

    assert calls == [store_a, store_b, None]


def test_product_list_does_not_mix_two_stores(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_a = database.add_store("店铺 A", "wx-product-a", "secret")
    store_b = database.add_store("店铺 B", "wx-product-b", "secret")
    for store_id, product_id, title in (
        (store_a, "a-product", "店铺 A 商品"),
        (store_b, "b-product", "店铺 B 商品"),
    ):
        database.upsert_product(
            Product(
                store_id,
                "",
                product_id,
                title,
                5,
                "销售中",
                0,
                1000,
                1,
                10,
                "",
                "",
                "",
            )
        )
    controller = NextUiController(database)

    assert [
        row["product_id"]
        for row in controller.dispatch(
            "products.list",
            {"store_id": store_a},
        )["products"]
    ] == ["a-product"]
    assert [
        row["product_id"]
        for row in controller.dispatch(
            "products.list",
            {"store_id": store_b},
        )["products"]
    ] == ["b-product"]
    assert {
        row["product_id"]
        for row in controller.dispatch(
            "products.list",
            {"store_id": "__all__"},
        )["products"]
    } == {"a-product", "b-product"}


def test_procurement_account_crud_is_available_to_web_ui(tmp_path: Path) -> None:
    controller = NextUiController(
        Database(tmp_path / "webui.db"),
        auth_context=_internal_auth_context(),
    )

    created = controller.dispatch(
        "procurement.create",
        {
            "platform": "淘宝",
            "label": "采购一号",
            "nickname": "采购昵称",
            "group_name": "默认",
            "remark": "测试",
        },
    )
    account_id = created["account"]["id"]
    controller.dispatch(
        "procurement.update",
        {"account_id": account_id, "label": "采购二号"},
    )
    payload = controller.dispatch("procurement.list", {})
    assert payload["accounts"][0]["label"] == "采购二号"

    controller.dispatch("procurement.delete", {"account_id": account_id})
    assert controller.dispatch("procurement.list", {})["accounts"] == []


def test_procurement_login_and_sync_use_existing_service(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "采购账号",
        "buyer-login",
    )
    service = FakeProductService()
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=_internal_auth_context(),
    )

    login = controller.dispatch(
        "procurement.login",
        {"account_id": account_id},
    )
    sync = controller.dispatch(
        "procurement.sync",
        {"account_id": account_id},
    )

    assert login["result"]["success"] is True
    assert sync["result"]["message"] == "采购数据已同步"
    assert service.procurement_logins == [account_id]
    assert service.procurement_syncs == [account_id]


def test_order_workbench_is_a_secondary_native_action(tmp_path: Path) -> None:
    calls = []
    controller = NextUiController(
        Database(tmp_path / "webui.db"),
        native_action=lambda action, payload: calls.append((action, payload)) or {
            "opened": True
        },
    )

    result = controller.dispatch(
        "workbench.open",
        {"store_id": 3, "order_id": "order-1"},
    )

    assert result["opened"] is True
    assert calls == [("workbench.open", {"store_id": 3, "order_id": "order-1"})]


def test_store_score_trends_compare_with_previous_cached_score(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("评分店铺", "wx-score-trend", "secret")
    database.set_setting(
        f"webui_store_score_state_{store_id}",
        json.dumps(
            {
                "version": 3,
                "values": {},
                "changes": {},
                "daily": {
                    "2026-11-21": {
                        "dsr": {"value": 4.8},
                        "score_product": {"value": 4.6},
                    }
                },
                "refresh": {},
                "last_attempt_at": "",
            }
        ),
    )
    controller = NextUiController(
        database,
        now_provider=lambda: datetime(2026, 11, 22, 10, 5, 0),
    )
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps({"dsr": 4.8, "score_product": 4.6}),
    )
    controller._record_store_score_baseline({store_id})

    refreshed = {
        "dsr": 4.9,
        "score_product": 4.5,
        "_score_attempt_status": {
            "dsr": "updated",
            "score_product": "updated",
            "score_deliver": "missing",
            "score_service": "missing",
        },
        "_score_updated_at": "2026-11-22 10:00:00",
    }
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(refreshed),
    )
    controller._record_store_score_refresh_result(store_id, refreshed)
    payload = controller.dispatch("stores.list", {})
    repeated = controller.dispatch("stores.list", {})

    trends = payload["stores"][0]["score_trends"]
    assert trends["dsr"] == 0.1
    assert trends["score_product"] == -0.1
    assert repeated["stores"][0]["score_trends"] == trends


def test_store_score_trends_do_not_invent_direction_when_unchanged(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("稳定评分店铺", "wx-stable-score", "secret")
    metrics = {
        "dsr": 4.8,
        "score_product": 4.7,
        "score_deliver": 4.6,
        "score_service": 4.9,
    }
    database.set_setting(f"store_metrics_{store_id}", json.dumps(metrics))
    controller = NextUiController(database)
    controller._record_store_score_baseline({store_id})

    refreshed = {
        **metrics,
        "_score_attempt_status": {
            key: "updated"
            for key in ("dsr", "score_product", "score_deliver", "score_service")
        },
        "_score_updated_at": "2026-11-22 10:00:00",
    }
    database.set_setting(f"store_metrics_{store_id}", json.dumps(refreshed))
    controller._record_store_score_refresh_result(store_id, refreshed)
    payload = controller.dispatch("stores.list", {})

    assert payload["stores"][0]["score_trends"] == {}


def test_store_refresh_uses_api_even_when_web_login_is_missing(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("未扫码店铺", "wx-api-only", "secret")
    database.update_web_status(store_id, "未登录", "未扫码")

    class RefreshService:
        def __init__(self) -> None:
            self.refreshed: list[int] = []

        def refresh_store_metrics(self, store):
            self.refreshed.append(store.id)
            return dict(store=None)

    service = RefreshService()
    controller = NextUiController(database, product_service=service)
    payload = controller.dispatch("stores.refresh", {})

    assert service.refreshed == [store_id]
    assert payload["refreshed"] == 1
    assert payload["succeeded"] == 1


def test_store_refresh_job_reports_live_completed_count(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    first_id = database.add_store("进度店铺一", "wx-store-progress-one", "secret")
    second_id = database.add_store("进度店铺二", "wx-store-progress-two", "secret")
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    started = controller.dispatch("stores.refresh.start", {})
    for _ in range(500):
        status = controller.dispatch(
            "stores.refresh.status",
            {"job_id": started["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "completed"
    assert status["current"] == 2
    assert status["total"] == 2
    assert status["succeeded"] == 2
    assert sorted(service.store_refreshes) == sorted([first_id, second_id])
    assert controller.wait_for_store_refresh(
        started["job_id"],
        timeout=5,
    )


def test_product_refresh_job_can_refresh_selected_or_all_status_tabs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("多状态店铺", "wx-product-tabs", "secret")
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    started = controller.dispatch(
        "products.refresh.start",
        {
            "store_id": store_id,
            "tab_keys": ["sales", "offline", "recycle"],
        },
    )
    for _ in range(100):
        status = controller.dispatch(
            "products.refresh.status",
            {"job_id": started["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "completed"
    assert status["tab_keys"] == ["sales", "offline", "recycle"]
    assert service.product_refreshes == [
        (store_id, "sales"),
        (store_id, "offline"),
        (store_id, "recycle"),
    ]


def test_order_and_product_sync_actions_call_api_service(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("API店铺", "wx-api-sync", "secret")
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    order_result = controller.dispatch(
        "orders.sync",
        {"store_id": store_id},
    )
    product_result = controller.dispatch(
        "products.refresh",
        {"store_id": store_id, "tab_key": "sales"},
    )

    assert order_result["succeeded"] == 1
    assert product_result["succeeded"] == 1
    assert service.order_syncs[0][0] == store_id
    assert service.product_refreshes == [(store_id, "sales")]


def test_order_quick_refresh_job_uses_network_sync_and_reports_progress(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_id = database.add_store("快速店铺一", "wx-quick-order-one", "secret")
    second_id = database.add_store("快速店铺二", "wx-quick-order-two", "secret")
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    started = controller.dispatch("orders.quick-refresh.start", {})
    for _ in range(100):
        status = controller.dispatch(
            "orders.quick-refresh.status",
            {"job_id": started["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "completed"
    assert status["current"] == 2
    assert status["total"] == 2
    assert status["succeeded"] == 2
    assert status["updated"] == 6
    assert all(
        end_time - start_time == 48 * 3600
        for _store_id, start_time, end_time in service.order_syncs
    )
    assert sorted(item[0] for item in service.order_syncs) == sorted(
        [first_id, second_id]
    )


def test_order_marks_filters_note_and_decrypt_are_wired(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("订单店铺", "wx-order-actions", "secret")
    order_row = {
        "store_id": store_id,
        "store_name": "订单店铺",
        "order_id": "order-1",
        "status": 21,
        "status_text": "待发货",
        "create_time": 1_700_000_000,
        "total_amount": 1990,
    }
    database.list_orders = lambda **_kwargs: [order_row]
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    assert controller.dispatch("orders.list", {})["orders"][0]["is_marked"] is False
    marked = controller.dispatch(
        "orders.mark",
        {"order_id": "order-1", "marked": True},
    )
    assert marked["is_marked"] is True
    assert controller.dispatch("orders.list", {})["orders"][0]["is_marked"] is True

    controller.dispatch(
        "orders.filters.save",
        {"hidden_product_ids": ["product-1", " product-2 ", ""]},
    )
    assert controller.dispatch("orders.filters.get", {})["hidden_product_ids"] == [
        "product-1",
        "product-2",
    ]

    controller.dispatch(
        "orders.update-note",
        {
            "store_id": store_id,
            "order_id": "order-1",
            "notes": "已联系仓库",
        },
    )
    controller.dispatch(
        "orders.decrypt-address",
        {"store_id": store_id, "order_id": "order-1"},
    )
    controller.dispatch(
        "orders.settings.save",
        {"settings": {"print_mode": "本地打印", "gift_auto_reship": False}},
    )
    assert service.order_notes == [(store_id, "order-1", "已联系仓库", 0)]
    assert service.decrypted_orders == [(store_id, "order-1")]
    assert controller.dispatch("orders.settings.get", {})["settings"] == {
        "print_mode": "本地打印",
        "gift_auto_reship": False,
    }


def test_orders_list_returns_absolute_shipping_deadline_and_server_time(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("倒计时店铺", "wx-countdown", "secret")
    platform_deadline = 1_900_000_000
    rows = [
        {
            "store_id": store_id,
            "store_name": "倒计时店铺",
            "order_id": "deadline-platform",
            "status": 20,
            "create_time": 1_899_000_000,
            "pay_time": 1_899_000_100,
            "raw_json": json.dumps(
                {
                    "order_detail": {
                        "product_infos": [
                            {"delivery_deadline": platform_deadline},
                        ],
                        "delivery_info": {
                            "predict_delivery_time": platform_deadline + 100,
                        },
                    }
                }
            ),
        },
        {
            "store_id": store_id,
            "store_name": "倒计时店铺",
            "order_id": "deadline-fallback",
            "status": 20,
            "create_time": 1_899_000_000,
            "pay_time": 1_899_001_000,
            "raw_json": "{}",
        },
        {
            "store_id": store_id,
            "store_name": "倒计时店铺",
            "order_id": "deadline-not-applicable",
            "status": 30,
            "create_time": 1_899_000_000,
            "pay_time": 1_899_001_000,
            "raw_json": json.dumps(
                {
                    "order_detail": {
                        "product_infos": [
                            {"delivery_deadline": platform_deadline},
                        ],
                    }
                }
            ),
        },
    ]
    database.list_orders = lambda **_kwargs: rows
    result = NextUiController(database).dispatch("orders.list", {})

    by_id = {row["order_id"]: row for row in result["orders"]}
    assert by_id["deadline-platform"]["shipping_deadline_at"] == platform_deadline
    assert by_id["deadline-fallback"]["shipping_deadline_at"] == (
        1_899_001_000 + 48 * 3600
    )
    assert by_id["deadline-not-applicable"]["shipping_deadline_at"] == 0
    assert isinstance(result["server_time"], int)
    assert result["server_time"] > 1_700_000_000


def test_order_flag_and_software_note_state_are_persisted(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("备注店铺", "wx-note-state", "secret")
    order_row = {
        "store_id": store_id,
        "store_name": "备注店铺",
        "order_id": "note-order-1",
        "status": 21,
        "status_text": "待发货",
        "create_time": 1_700_000_000,
        "total_amount": 1990,
        "merchant_notes": "",
    }
    database.list_orders = lambda **_kwargs: [order_row]
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    result = controller.dispatch(
        "orders.update-note-state",
        {
            "store_id": store_id,
            "order_id": "note-order-1",
            "scope": "软件备注",
            "flag": "红",
            "note": "软件内查看",
            "sync_store": False,
        },
    )
    listed = controller.dispatch("orders.list", {})["orders"][0]

    assert result["software_note"] == "软件内查看"
    assert result["flag"] == "红"
    assert result["synced_to_store"] is False
    assert listed["software_note"] == "软件内查看"
    assert listed["flag"] == "红"
    assert service.order_notes == []


def test_store_order_note_and_official_flag_overwrite_local_display(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("店铺备注店", "wx-store-note-sync", "secret")
    order_row = {
        "store_id": store_id,
        "store_name": "店铺备注店",
        "order_id": "store-note-order",
        "status": 21,
        "status_text": "待发货",
        "create_time": 1_700_000_000,
        "total_amount": 1990,
        "merchant_notes": "此单不发货",
        "raw_json": json.dumps(
            {
                "order_detail": {
                    "ext_info": {"merchant_notes_tag_color": 1},
                }
            },
            ensure_ascii=False,
        ),
    }
    database.list_orders = lambda **_kwargs: [order_row]
    service = FakeProductService()
    controller = NextUiController(
        database,
        product_service=service,
    )

    listed = controller.dispatch("orders.list", {})["orders"][0]

    assert listed["merchant_notes"] == "此单不发货"
    assert listed["software_note"] == ""
    assert listed["official_note_color"] == "红"
    assert listed["flag"] == "红"

    synced = controller.dispatch(
        "orders.update-note-state",
        {
            "store_id": store_id,
            "order_id": "store-note-order",
            "scope": "店铺备注",
            "flag": "蓝",
            "note": "已联系仓库",
            "sync_store": True,
        },
    )
    assert synced["synced_to_store"] is True
    assert service.order_notes == [
        (store_id, "store-note-order", "已联系仓库", 4)
    ]


def test_order_workbench_manual_purchase_and_detail_are_persisted(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("工作台店铺", "wx-workbench", "secret")
    database.upsert_order(
        Order(
            store_id=store_id,
            store_name="工作台店铺",
            order_id="workbench-order-1",
            status=20,
            status_text="待采购",
            create_time=1_700_000_000,
            update_time=1_700_000_010,
            pay_time=1_700_000_005,
            total_amount=3990,
            product_amount=3990,
            freight_amount=0,
            discount_amount=0,
            buyer_name="测试买家",
            phone="13800000000",
            address="测试地址",
            waybill_id="",
            carrier="",
            merchant_notes="",
            items=[
                OrderItem(
                    product_id="product-1",
                    sku_id="sku-1",
                    title="测试商品",
                    sku_count=1,
                    sale_price=3990,
                    thumb_url="",
                )
            ],
            raw={},
        )
    )
    service = FakeProductService()
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=_internal_auth_context(),
    )

    saved = controller.dispatch(
        "orders.workbench.manual-purchase",
        {
            "store_id": store_id,
            "order_id": "workbench-order-1",
            "platform": "1688",
            "purchase_order_no": "PURCHASE-1",
            "purchase_amount_cents": 1234,
            "commission": 5,
            "purchase_mode": "手动下单",
            "transfer_warehouse": False,
            "flag": "蓝",
        },
    )
    detail = controller.dispatch(
        "orders.workbench.detail",
        {"store_id": store_id, "order_id": "workbench-order-1"},
    )

    assert saved["purchase_order_no"] == "PURCHASE-1"
    assert detail["workbench"]["purchase_order_no"] == "PURCHASE-1"
    assert detail["workbench"]["cost"] == 1234
    assert detail["workbench"]["flag_color"] == "蓝"
    assert detail["procurement_links"][0]["external_order_id"] == "PURCHASE-1"
    listed = controller.dispatch(
        "orders.list",
        {"store_id": store_id},
    )["orders"][0]
    assert listed["purchase_order_no"] == "PURCHASE-1"
    assert listed["procurement_count"] == 1


def test_webui_platform_write_authorizer_is_bound_to_controller_scope(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("安全门店铺", "wx-write-gate", "secret")
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)
    database_store = database.get_store(store_id)

    assert database_store is not None
    try:
        service.update_order_notes(database_store, "order-1", "不应写入", 0)
    except PermissionError as exc:
        assert "缺少当前会话安全门" in str(exc)
    else:
        raise AssertionError("controller 作用域外的平台写操作必须被拒绝")

    controller.dispatch(
        "orders.update-note-state",
        {
            "store_id": store_id,
            "order_id": "order-1",
            "scope": "店铺备注",
            "flag": "红",
            "note": "允许写入",
            "sync_store": False,
        },
    )
    assert service.order_notes == [(store_id, "order-1", "允许写入", 1)]


def test_order_import_file_parser_reads_csv(tmp_path: Path) -> None:
    controller = NextUiController(Database(tmp_path / "webui.db"))
    content = "订单号,采购单号,取件码,备注\nORDER-1,PURCHASE-1,A-001,测试备注\n"

    result = controller.dispatch(
        "orders.import-file.parse",
        {
            "file_name": "orders.csv",
            "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        },
    )

    assert result["records"] == [
        {
            "订单号": "ORDER-1",
            "采购单号": "PURCHASE-1",
            "取件码": "A-001",
            "备注": "测试备注",
        }
    ]


def test_auto_after_sale_uses_saved_rules_and_controller_write_scope(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("自动售后店", "wx-auto-after-sale", "secret")
    database.upsert_after_sale(
        store_id,
        {
            "after_sale_order_id": "after-sale-1",
            "order_id": "order-1",
            "status": "MERCHANT_PROCESSING",
            "type": "仅退款",
            "refund_info": {"amount": 1200},
            "create_time": 1_700_000_000,
            "update_time": 1_700_000_010,
        },
    )
    service = FakeProductService()
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=_internal_auth_context(),
    )
    controller.dispatch(
        "orders.after-sales.auto-config.save",
        {
            "config": {
                "enabled": True,
                "action": "accept",
                "max_amount": 2000,
                "allowed_types": "仅退款",
            }
        },
    )

    result = controller.dispatch(
        "orders.after-sales.auto-start",
        {"store_id": store_id},
    )
    detail = controller.dispatch(
        "orders.after-sales.detail",
        {
            "store_id": store_id,
            "after_sale_id": "after-sale-1",
        },
    )

    assert result["eligible"] == 1
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert detail["after_sale"]["order_id"] == "order-1"
    assert detail["raw"]["after_sale_order_id"] == "after-sale-1"


def test_profit_report_uses_real_purchase_cost_and_flags_pending_orders(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("利润店铺", "wx-profit-report", "secret")
    created = int(datetime(2026, 11, 20, 10, 0, 0).timestamp())
    for order_id, amount in (("profit-1", 10000), ("profit-2", 5000)):
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name="利润店铺",
                order_id=order_id,
                status=100,
                status_text="已完成",
                create_time=created,
                update_time=created,
                pay_time=created,
                total_amount=amount,
                product_amount=amount,
                freight_amount=0,
                discount_amount=0,
                buyer_name="买家",
                phone="13800000000",
                address="地址",
                waybill_id="",
                carrier="",
                merchant_notes="",
                items=[
                    OrderItem(
                        product_id="product-profit",
                        sku_id="sku-profit",
                        title="利润商品",
                        sku_count=1,
                        sale_price=amount,
                        thumb_url="",
                    )
                ],
                raw={
                    "order_detail": {
                        "commission": 500,
                        "platform_fee": 200,
                        "product_infos": [
                            {
                                "product_id": "product-profit",
                                "sku_cnt": 1,
                                "title": "利润商品",
                            }
                        ],
                    }
                },
            )
        )
    service = FakeProductService()
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=_internal_auth_context(),
    )
    controller.dispatch(
        "orders.workbench.manual-purchase",
        {
            "store_id": store_id,
            "order_id": "profit-1",
            "platform": "1688",
            "purchase_order_no": "PURCHASE-PROFIT",
            "purchase_amount_cents": 4000,
            "commission": 0,
            "purchase_mode": "手动下单",
            "transfer_warehouse": False,
            "goods_title": "利润商品",
            "goods_count": 1,
        },
    )

    report = controller.dispatch(
        "orders.profit.report",
        {
            "view": "利润统计",
            "period": "订单报表",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
    )

    assert report["summary"]["orders"] == 2
    assert report["summary"]["sale"] == 15000
    assert report["summary"]["cost"] == 4000
    assert report["summary"]["commission"] == 1000
    assert report["summary"]["platform_fee"] == 400
    assert report["summary"]["profit"] == 5300
    assert report["summary"]["pending"] == 1
    pending_row = next(row for row in report["orders"] if row["order_id"] == "profit-2")
    assert pending_row["pending"] is True

    store_report = controller.dispatch(
        "orders.profit.report",
        {
            "view": "店铺统计",
            "period": "月报表",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
    )
    product_report = controller.dispatch(
        "orders.profit.report",
        {
            "view": "商品统计",
            "period": "年报表",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
    )
    assert store_report["aggregate"][0]["label"] == "利润店铺"
    assert store_report["aggregate"][0]["orders"] == 2
    assert product_report["aggregate"][0]["label"] == "product-profit"


def test_violation_list_and_refresh_use_persisted_records(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("违规店铺", "wx-violation", "secret")
    database.update_web_status(store_id, "已登录", "")
    database.set_setting(
        "violation_library",
        json.dumps(
            [
                {
                    "record_id": "record-1",
                    "store_id": store_id,
                    "store_name": "违规店铺",
                    "reason": "测试违规原因",
                    "status": "待处理",
                    "head_img": "https://example.test/violation.jpg",
                }
            ],
            ensure_ascii=False,
        ),
    )
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    listed = controller.dispatch("violations.list", {})
    refreshed = controller.dispatch("violations.refresh", {"store_id": store_id})

    assert listed["violations"][0]["record_id"] == "record-1"
    assert (
        listed["violations"][0]["head_img"]
        == "https://example.test/violation.jpg"
    )
    assert refreshed["succeeded"] == 1
    assert service.violation_syncs == [store_id]


def test_upload_preflight_blocks_exact_counterfeit_violation_title(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store(
        "违规预检店铺",
        "wx-counterfeit-preflight",
        "secret",
    )
    database.update_web_status(store_id, "已登录", "")
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": "batch-1",
                    "title": "毛绒花束捧花｜520生日礼物",
                    "platform": "淘宝",
                    "source_product_id": "source-1",
                    "price": 100,
                    "stock": 5,
                    "payload": {
                        "head_imgs": ["https://example.test/head.jpg"],
                        "skus": [{"sale_price": 100, "stock_num": 5}],
                    },
                }
            ],
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        "upload_templates",
        json.dumps(
            [
                {
                    "id": "tpl-cloud",
                    "name": "云违规过滤",
                    "cloud_violation": True,
                }
            ],
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        "upload_violation_titles",
        json.dumps(
            [
                {
                    "title": "毛绒花束捧花｜520生日礼物",
                    "normalized": "毛绒花束捧花｜520生日礼物",
                    "reason": "出售假冒商品",
                    "store_id": store_id,
                    "store_app_id": "wx-counterfeit-preflight",
                }
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)

    result = controller.dispatch(
        "upload.batch.preflight",
        {"batch_ids": ["batch-1"], "template_id": "tpl-cloud"},
    )

    assert result["ready"] == 0
    assert result["blocked"] == 1
    assert "云违规标题过滤命中：出售假冒商品" in result["rows"][0]["issues"]


def test_upload_templates_are_loaded_and_saved_from_existing_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    database.set_setting(
        "upload_templates",
        json.dumps([{"id": "existing", "name": "已有模板"}], ensure_ascii=False),
    )
    database.set_setting(
        "upload_tasks",
        json.dumps([{"id": "task-1", "status": "partial", "items": []}]),
    )
    controller = NextUiController(database)

    listed = controller.dispatch("upload.list", {})
    saved = controller.dispatch(
        "upload.templates.save",
        {"template": {"id": "existing", "name": "修改后模板"}},
    )
    deleted = controller.dispatch(
        "upload.templates.delete",
        {"template_id": "existing"},
    )

    assert listed["templates"][0]["name"] == "已有模板"
    assert listed["tasks"][0]["id"] == "task-1"
    assert saved["template"]["name"] == "修改后模板"
    assert deleted["templates"] == []


def test_webui_upload_imports_three_platforms_deduplicates_and_exports_safely(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    files = [
        {
            "name": "taobao.json",
            "content": json.dumps(
                {
                    "platform": "淘宝",
                    "productId": "TB-100",
                    "productName": "淘宝测试商品",
                    "priceLow": "10.00",
                    "stock": 8,
                    "itemImgs": ["https://img.alicdn.com/tb-main.jpg"],
                    "skus": [{"price": "1000", "quantity": "8", "id": "1"}],
                },
                ensure_ascii=False,
            ),
        },
        {
            "name": "wechat.json",
            "content": json.dumps(
                {
                    "platform": "微信小店",
                    "product_id": "WX-100",
                    "title": "微信测试商品",
                    "price": 12,
                    "stock": 6,
                    "headImg": ["https://wst.example.test/main.jpg"],
                    "skus": [{"sale_price": 1200, "stock_num": 6}],
                },
                ensure_ascii=False,
            ),
        },
        {
            "name": "1688.json",
            "content": json.dumps(
                {
                    "platform": "1688",
                    "offer_id": "1688-100",
                    "title": "1688测试商品",
                    "head_imgs": ["https://cbu01.alicdn.com/main.jpg"],
                    "skus": [
                        {
                            "out_sku_id": "SKU-1",
                            "sale_price": 990,
                            "stock_num": 9,
                            "sku_attrs": [
                                {"attr_key": "规格", "attr_value": "默认"}
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]

    imported = controller.dispatch(
        "upload.batch.import",
        {"files": files, "append": False},
    )
    repeated = controller.dispatch(
        "upload.batch.import",
        {"files": files, "append": True},
    )
    preflight = controller.dispatch("upload.batch.preflight", {})

    assert imported["imported"] == 3
    assert {row["platform"] for row in imported["batch"]} == {
        "1688",
        "淘宝",
        "微信小店",
    }
    assert repeated["imported"] == 0
    assert repeated["skipped"] == 3
    assert len(repeated["batch"]) == 3
    assert preflight["checked"] == 3
    assert preflight["blocked"] == 0
    assert {
        row["mapping"]["sku_count"]
        for row in preflight["rows"]
    } == {1}
    assert {
        row["platform"]: row["mapping"]["category_mode"]
        for row in preflight["rows"]
    } == {
        "1688": "auto",
        "淘宝": "auto",
        "微信小店": "auto",
    }

    batch = controller._upload_batch_rows()
    batch[0]["appsecret"] = "must-not-export"
    batch[0]["nested"] = {
        "cookie": "must-not-export",
        "access_token": "must-not-export",
        "safe": "visible",
    }
    batch[0]["address_info"] = {"detail": "must-not-export"}
    controller._save_upload_batch(batch)
    exported = controller.dispatch("upload.batch.export", {})

    assert "must-not-export" not in exported["content"]
    assert '"safe": "visible"' in exported["content"]


def test_webui_upload_task_creation_snapshot_isolation_retry_and_logs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_store = database.add_store("任务店铺一", "wx-upload-one", "secret")
    second_store = database.add_store("任务店铺二", "wx-upload-two", "secret")
    controller = NextUiController(database)
    imported = controller.dispatch(
        "upload.batch.import",
        {
            "append": False,
            "files": [
                {
                    "name": "batch.json",
                    "content": json.dumps(
                        {
                            "products": [
                                {
                                    "platform": "淘宝",
                                    "productId": "TB-TASK-1",
                                    "productName": "任务商品一",
                                    "price": 1000,
                                    "stock": 5,
                                    "itemImgs": [
                                        "https://img.alicdn.com/task-1.jpg"
                                    ],
                                        "desc": [
                                            "https://img.alicdn.com/detail-1.jpg",
                                            "https://img.alicdn.com/detail-2.jpg",
                                            "https://img.alicdn.com/detail-3.jpg",
                                            "https://img.alicdn.com/detail-4.jpg",
                                            "https://img.alicdn.com/detail-5.jpg",
                                            "https://img.alicdn.com/detail-6.jpg",
                                        ],
                                    "skus": [
                                        {
                                            "price": "1000",
                                            "quantity": "5",
                                            "id": "1",
                                        }
                                    ],
                                },
                                {
                                    "platform": "微信小店",
                                    "product_id": "WX-TASK-2",
                                    "title": "任务商品二",
                                    "price": 20,
                                    "stock": 4,
                                    "headImg": [
                                        "https://wst.example.test/task-2.jpg"
                                    ],
                                        "desc": [
                                            "https://wst.example.test/detail-1.jpg",
                                            "https://wst.example.test/detail-2.jpg",
                                            "https://wst.example.test/detail-3.jpg",
                                            "https://wst.example.test/detail-4.jpg",
                                            "https://wst.example.test/detail-5.jpg",
                                            "https://wst.example.test/detail-6.jpg",
                                        ],
                                        "desc_info": {
                                            "imgs": [
                                                "https://wst.example.test/detail-1.jpg",
                                                "https://wst.example.test/detail-2.jpg",
                                                "https://wst.example.test/detail-3.jpg",
                                                "https://wst.example.test/detail-4.jpg",
                                                "https://wst.example.test/detail-5.jpg",
                                                "https://wst.example.test/detail-6.jpg",
                                            ],
                                            "desc": "",
                                        },
                                    "skus": [
                                        {
                                            "sale_price": 2000,
                                            "stock_num": 4,
                                        }
                                    ],
                                },
                            ]
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        },
    )
    saved = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                "name": "完整任务模板",
                "price_multiplier": 1.2,
                "price_offset": 50,
                "stock": 20,
                "listing_mode": "draft",
                "duplicate_filter_mode": "store",
                "auto_open_category": True,
                "image_rules": "保留前三张主图",
                "sku_rules": "按来源 SKU 映射",
                "attribute_rules": "保留来源属性",
            }
        },
    )
    template_id = saved["template"]["id"]
    batch_ids = [row["batch_id"] for row in imported["batch"]]
    created = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": batch_ids,
            "store_ids": [first_store, second_store],
            "template_id": template_id,
        },
    )

    assert created["created"] == 2
    tasks = created["tasks"]
    assert {task["store_id"] for task in tasks} == {first_store, second_store}
    assert all(len(task["items"]) == 2 for task in tasks)
    assert all(
        task["template"]["name"] == "完整任务模板"
        for task in tasks
    )
    assert all(
        task["template"]["sku_rules"] == "按来源 SKU 映射"
        for task in tasks
    )
    assert {
        task["store_id"]: {
            item["source_product_id"] for item in task["items"]
        }
        for task in tasks
    } == {
        first_store: {"TB-TASK-1", "WX-TASK-2"},
        second_store: {"TB-TASK-1", "WX-TASK-2"},
    }

    first_task = tasks[0]
    first_task["items"][0]["status"] = "failed"
    first_task["items"][0]["message"] = "明确失败"
    first_task["items"][1]["status"] = "verification_pending"
    first_task["items"][1]["message"] = "结果未知"
    tasks[0] = first_task
    controller._save_upload_tasks(tasks)
    retried = controller.dispatch(
        "upload.tasks.retry",
        {"task_ids": [first_task["id"]]},
    )

    refreshed = next(
        task for task in retried["tasks"] if task["id"] == first_task["id"]
    )
    assert retried["retried"] == 1
    assert refreshed["items"][0]["status"] == "pending"
    assert refreshed["items"][1]["status"] == "verification_pending"
    logs = controller.dispatch(
        "upload.logs",
        {"task_id": first_task["id"], "status": "verification_pending"},
    )
    assert logs["count"] == 1
    assert logs["logs"][0]["message"] == "结果未知"
    assert "raw" not in logs["logs"][0]
    assert "payload" not in logs["logs"][0]

    changed_template = dict(saved["template"])
    changed_template["name"] = "修改后模板"
    controller.dispatch(
        "upload.templates.save",
        {"template": changed_template},
    )
    persisted = controller.dispatch("upload.list", {})["tasks"]
    assert next(
        task for task in persisted if task["id"] == first_task["id"]
    )["template"]["name"] == "完整任务模板"


def test_procurement_login_scan_only_does_not_autofill_saved_credentials(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    account_id = database.add_procurement_account(
        "1688",
        "采购账号",
        "buyer-login",
    )

    class FakeService:
        def __init__(self) -> None:
            self.calls = []

        def procurement_login(self, account_id: int, *, auto_fill: bool = True):
            self.calls.append((account_id, auto_fill))
            return {"status": "待验证"}

    service = FakeService()
    controller = NextUiController(
        database,
        product_service=service,
        auth_context=_internal_auth_context(),
    )
    result = controller.dispatch(
        "procurement.login",
        {"account_id": account_id, "scan_only": True},
    )

    assert result["account_id"] == account_id
    assert service.calls == [(account_id, False)]


def test_webui_upload_pause_and_cancel_are_deferred_requests(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)

    class Runner:
        def __init__(self) -> None:
            self.paused = threading.Event()
            self.cancelled = threading.Event()

    runner = Runner()
    controller._upload_jobs["job-1"] = {
        "job_id": "job-1",
        "kind": "run",
        "status": "running",
        "message": "运行中",
        "runner": runner,
        "errors": [],
    }
    paused = controller.dispatch(
        "upload.run.pause",
        {"job_id": "job-1"},
    )
    assert runner.paused.is_set()
    assert paused["status"] == "pausing"
    assert "当前商品处理结束" in paused["message"]

    cancelled = controller.dispatch(
        "upload.run.cancel",
        {"job_id": "job-1"},
    )
    assert runner.cancelled.is_set()
    assert cancelled["status"] == "cancelling"
    assert "在途商品处理结束" in cancelled["message"]
    assert "runner" not in cancelled


def test_webui_upload_list_restores_active_run_and_controls_preparing_job(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    controller._upload_jobs["job-preparing"] = {
        "job_id": "job-preparing",
        "kind": "run",
        "status": "starting",
        "message": "正在准备上传任务…",
        "completed": 0,
        "total": 160,
        "errors": [],
    }

    listed = controller.dispatch("upload.list", {})
    assert listed["active_run_job"]["job_id"] == "job-preparing"
    assert listed["active_run_job"]["status"] == "starting"

    paused = controller.dispatch(
        "upload.run.pause",
        {"job_id": "job-preparing"},
    )
    assert paused["status"] == "pausing"
    assert controller._upload_jobs["job-preparing"]["pause_requested"] is True

    cancelled = controller.dispatch(
        "upload.run.cancel",
        {"job_id": "job-preparing"},
    )
    assert cancelled["status"] == "cancelling"
    assert controller._upload_jobs["job-preparing"]["cancel_requested"] is True


def test_webui_upload_never_starts_verification_pending_items(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("待核实店铺", "wx-verification", "secret")
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-verification",
                    "store_id": store_id,
                    "store_name": "待核实店铺",
                    "status": "needs_verification",
                    "template": {"name": "待核实模板"},
                    "items": [
                        {
                            "item_id": "item-verification",
                            "source_product_id": "SOURCE-UNKNOWN",
                            "title": "结果未知商品",
                            "status": "verification_pending",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(
        database,
        product_service=FakeProductService(),
    )

    try:
        controller.dispatch(
            "upload.run.start",
            {"task_ids": ["task-verification"]},
        )
    except ValueError as exc:
        assert "待核实" in str(exc)
    else:
        raise AssertionError("verification_pending must never be resubmitted")


def test_webui_upload_retry_accepts_only_pre_submit_permission_rejections(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("授权重试店铺", "wx-retry-auth", "secret")
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-retry-auth",
                    "store_id": store_id,
                    "store_name": "授权重试店铺",
                    "status": "needs_verification",
                    "template": {"name": "重试模板"},
                    "items": [
                        {
                            "item_id": "safe-rejection",
                            "source_product_id": "SAFE-REJECTION",
                            "status": "verification_pending",
                            "product_id": "",
                            "message": (
                                "远端提交结果未知，请先核实："
                                "真实平台写操作缺少当前会话安全门，"
                                "已拒绝执行：product_image_upload"
                            ),
                        },
                        {
                            "item_id": "unknown-timeout",
                            "source_product_id": "UNKNOWN-TIMEOUT",
                            "status": "verification_pending",
                            "product_id": "",
                            "message": "远端提交结果未知，请先核实：timeout",
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)

    result = controller.dispatch(
        "upload.tasks.retry",
        {"task_ids": ["task-retry-auth"]},
    )
    items = result["tasks"][0]["items"]

    assert result["retried"] == 1
    assert items[0]["status"] == "pending"
    assert items[1]["status"] == "verification_pending"


def test_webui_upload_product_service_uses_current_write_scope(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("写入店铺", "wx-upload-write", "secret")
    store = database.get_store(store_id)

    class GuardedService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.calls = []

        def product_write(self, current_store, action, payload, **_kwargs):
            self.platform_write_authorizer(
                current_store.app_id,
                "product_write",
            )
            self.calls.append((current_store.id, action, payload))
            return {"product_id": "PRODUCT-1"}

    service = GuardedService()
    controller = NextUiController(database, product_service=service)
    wrapped = _ScopedUploadProductService(controller, service)

    result = wrapped.product_write(store, "add", {"title": "商品"})

    assert result["product_id"] == "PRODUCT-1"
    assert service.calls == [(store_id, "add", {"title": "商品"})]


def test_webui_upload_run_rpc_executes_selected_persisted_task(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("执行店铺", "wx-upload-run", "secret")
    event = threading.Event()

    class GuardedService:
        def __init__(self) -> None:
            self.platform_write_authorizer = None
            self.calls = []

        def product_write(self, store, action, payload, **_kwargs):
            self.platform_write_authorizer(store.app_id, "product_write")
            self.calls.append((store.id, action, payload))
            return {"product_id": "WX-RUN-1"}

    class FakeRunner:
        def __init__(
            self,
            workspace,
            tasks,
            *,
            progress=None,
            external_stop_requested=None,
        ):
            self.workspace = workspace
            self.tasks = tasks
            self.progress = progress
            self.external_stop_requested = external_stop_requested
            self.paused = threading.Event()
            self.cancelled = threading.Event()

        def run(self, selected, *, selected_item_ids=None):
            item = selected[0]["items"][0]
            store = self.workspace.db.get_store(int(selected[0]["store_id"]))
            if self.progress:
                self.progress("上传任务处理中 1/1", 1, 1)
            result = self.workspace.service.product_write(
                store,
                "add",
                item["payload"],
            )
            item["status"] = "success"
            item["product_id"] = result["product_id"]
            item["message"] = "上传成功"
            selected[0]["status"] = "completed"
            self.workspace.db.set_setting(
                "upload_tasks",
                json.dumps(self.tasks, ensure_ascii=False),
            )
            event.set()
            return []

    service = GuardedService()
    controller = NextUiController(database, product_service=service)
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-run",
                    "store_id": store_id,
                    "store_name": "执行店铺",
                    "status": "pending",
                    "template": {
                        "name": "运行模板",
                        "auto_open_category": False,
                        "duplicate_filter_mode": "off",
                    },
                    "items": [
                        {
                            "item_id": "item-run",
                            "source_product_id": "SOURCE-RUN",
                            "title": "执行商品",
                            "status": "pending",
                            "payload": {"title": "执行商品"},
                        }
                    ],
                },
                {
                    "id": "task-unrelated-waiting",
                    "store_id": store_id,
                    "store_name": "执行店铺",
                    "status": "needs_category",
                    "template": {"name": "其他模板"},
                    "items": [
                        {
                            "item_id": "item-unrelated",
                            "title": "未选中类目商品",
                            "status": "waiting_category",
                        }
                    ],
                },
            ],
            ensure_ascii=False,
        ),
    )

    with mock.patch(
        "weimi_manager.pyside.pages.upload.UploadRunner",
        FakeRunner,
    ):
        started = controller.dispatch(
            "upload.run.start",
            {"task_ids": ["task-run"]},
        )
        assert event.wait(timeout=2)
        for _ in range(100):
            status = controller.dispatch(
                "upload.run.status",
                {"job_id": started["job_id"]},
            )
            if status["status"] in {"completed", "partial", "failed"}:
                break
            time.sleep(0.01)

    assert status["status"] == "completed"
    assert service.calls == [
        (store_id, "add", {"title": "执行商品"})
    ]
    persisted = controller.dispatch("upload.list", {})["tasks"]
    assert persisted[0]["items"][0]["status"] == "success"
    assert persisted[0]["items"][0]["product_id"] == "WX-RUN-1"


def test_webui_upload_tasks_group_rows_by_assigned_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_store = database.add_store("甲店", "wx-a", "secret")
    second_store = database.add_store("乙店", "wx-b", "secret")
    controller = NextUiController(database)
    template = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                **(
                    controller._upload_templates()[0]
                    if controller._upload_templates()
                    else {"name": "分配模板"}
                ),
                "name": "分配模板",
                "deposit_limit": 2000,
                "duplicate_filter_mode": "off",
            }
        },
    )["template"]
    database.set_setting(
        "upload_batch",
        json.dumps(
            [
                {
                    "batch_id": "batch-a",
                    "source_product_id": "SOURCE-A",
                    "title": "甲商品",
                    "status": "pending",
                    "payload": {"title": "甲商品"},
                },
                {
                    "batch_id": "batch-b",
                    "source_product_id": "SOURCE-B",
                    "title": "乙商品",
                    "status": "pending",
                    "payload": {"title": "乙商品"},
                },
            ],
            ensure_ascii=False,
        ),
    )

    created = controller.dispatch(
        "upload.tasks.create",
        {
            "batch_ids": ["batch-a", "batch-b"],
            "store_ids": [first_store, second_store],
            "template_id": template["id"],
            "assignments": {
                "batch-a": first_store,
                "batch-b": second_store,
            },
            "store_options": {
                str(first_store): {
                    "freight_template_id": "freight-a",
                    "brand_id": "brand-a",
                    "after_sale_address_id": "address-a",
                },
                str(second_store): {
                    "freight_template_id": "freight-b",
                    "brand_id": "brand-b",
                    "after_sale_address_id": "address-b",
                },
            },
        },
    )

    assert created["created"] == 2
    by_store = {
        task["store_id"]: task
        for task in created["tasks"]
        if task["store_id"] in {first_store, second_store}
    }
    assert [item["source_product_id"] for item in by_store[first_store]["items"]] == [
        "SOURCE-A"
    ]
    assert [item["source_product_id"] for item in by_store[second_store]["items"]] == [
        "SOURCE-B"
    ]
    assert by_store[first_store]["template"]["freight_template_id"] == "freight-a"
    assert by_store[first_store]["template"]["brand_id"] == "brand-a"
    assert by_store[first_store]["template"]["after_sale_address_id"] == "address-a"
    assert by_store[second_store]["template"]["freight_template_id"] == "freight-b"


def test_webui_upload_store_options_use_real_defaults(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("配置店铺", "wx-options", "secret")

    class Client:
        def fetch_freight_template_ids(self):
            return ["freight-1"]

        def fetch_freight_template_detail(self, template_id):
            return {
                "template_id": template_id,
                "name": "默认快递模板",
            }

    class Service:
        def client_for(self, _store):
            return Client()

        def _product_payload_defaults(self, _store, _client):
            return {
                "express_info": {"template_id": "freight-1"},
                "after_sale_info": {
                    "after_sale_address_id": "address-1",
                },
            }

    controller = NextUiController(database, product_service=Service())
    result = controller.dispatch(
        "upload.store-options",
        {"store_ids": [store_id]},
    )["stores"][str(store_id)]

    assert result["default_freight_template_id"] == "freight-1"
    freight_by_id = {
        item["id"]: item["label"]
        for item in result["freight_templates"]
    }
    assert freight_by_id["freight-1"] == "默认快递模板"
    assert list(freight_by_id) == ["freight-1"]
    assert result["default_after_sale_address_id"] == "address-1"
    assert result["after_sale_addresses"] == [
        {
            "id": "address-1",
            "label": "店铺当前默认售后地址",
        }
    ]
    assert result["brands"][0] == {
        "id": "2100000000",
        "label": "无品牌",
    }


def test_webui_upload_store_options_reject_stale_cross_store_template(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("单模板店铺", "wx-one-template", "secret")

    class Client:
        def fetch_freight_template_ids(self):
            return ["955819508004"]

        def fetch_freight_template_detail(self, template_id):
            return {
                "template_id": template_id,
                "name": "全国包邮（偏远地区除外）",
            }

    class Service:
        def client_for(self, _store):
            return Client()

        def _product_payload_defaults(self, _store, _client):
            return {
                "express_info": {"template_id": "1103871366004"},
                "after_sale_info": {
                    "after_sale_address_id": "75563108002",
                },
            }

    controller = NextUiController(database, product_service=Service())
    result = controller.dispatch(
        "upload.store-options",
        {"store_ids": [store_id]},
    )["stores"][str(store_id)]

    assert result["default_freight_template_id"] == ""
    assert result["freight_templates"] == [
        {
            "id": "955819508004",
            "label": "全国包邮（偏远地区除外）",
        }
    ]
    assert result["after_sale_addresses"] == [
        {
            "id": "75563108002",
            "label": "店铺当前默认售后地址",
        }
    ]
    assert "不属于当前店铺" in result["error"]


def test_webui_upload_waiting_category_schedules_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("类目店铺", "wx-category", "secret")
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-category",
                    "store_id": store_id,
                    "store_name": "类目店铺",
                    "status": "needs_category",
                    "template": {"name": "类目模板"},
                    "items": [
                        {
                            "item_id": "item-category",
                            "title": "等待类目商品",
                            "source_product_id": "SOURCE-CATEGORY",
                            "status": "waiting_category",
                            "category_state": "waiting",
                            "category_ids": ["3001"],
                            "category_apply_submitted": True,
                            "category_next_check_at": datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)
    calls = []
    monkeypatch.setenv("LANG_LANG_UPLOAD_CATEGORY_RETRY_SECONDS", "0.05")
    monkeypatch.setattr(
        controller,
        "_start_upload_job",
        lambda payload: calls.append(payload) or {"job_id": "resume-job"},
    )

    controller.dispatch("upload.list", {})
    for _ in range(30):
        if calls:
            break
        time.sleep(0.01)

    assert calls == [
        {
            "task_ids": ["task-category"],
            "item_ids": ["item-category"],
        }
    ]


def test_webui_category_auto_resume_can_be_disabled_and_cancelled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "webui-category-toggle.db")
    store_id = database.add_store("类目开关店铺", "wx-toggle", "secret")
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-toggle",
                    "store_id": store_id,
                    "store_name": "类目开关店铺",
                    "status": "needs_category",
                    "items": [
                        {
                            "item_id": "item-toggle",
                            "status": "waiting_category",
                            "category_state": "deposit_required",
                            "category_ids": ["3001"],
                            "category_next_check_at": datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)
    starts = []
    monkeypatch.setenv("LANG_LANG_UPLOAD_CATEGORY_RETRY_SECONDS", "0.05")
    monkeypatch.setattr(
        controller,
        "_start_upload_job",
        lambda payload: starts.append(payload) or {"job_id": "resume"},
    )

    disabled = controller.dispatch(
        "upload.category-auto-resume.set",
        {"enabled": False},
    )
    time.sleep(0.12)

    assert disabled["enabled"] is False
    assert starts == []
    assert controller.dispatch("upload.list", {})[
        "category_auto_resume_enabled"
    ] is False

    cancelled = controller.dispatch(
        "upload.waiting-category.cancel",
        {
            "task_ids": ["task-toggle"],
            "item_ids": ["item-toggle"],
        },
    )
    item = cancelled["tasks"][0]["items"][0]
    assert cancelled["cancelled"] == 1
    assert item["status"] == "cancelled"
    assert item["category_state"] == "cancelled"
    assert item["category_next_check_at"] == ""


def test_webui_auto_schedules_all_safe_legacy_6600145_items(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "webui-legacy-category.db")
    first_store = database.add_store("历史店铺一", "wx-history-one", "secret")
    second_store = database.add_store("历史店铺二", "wx-history-two", "secret")
    complete_payload = {
        "title": "历史类目商品",
        "head_imgs": [
            "https://example.invalid/1.jpg",
            "https://example.invalid/2.jpg",
            "https://example.invalid/3.jpg",
        ],
        "skus": [{"sale_price": 100, "stock_num": 2}],
        "cats_v2": [{"cat_id": "2000"}, {"cat_id": "3001"}],
    }
    history_items = [
        {
            "item_id": f"history-{index}",
            "status": "failed",
            "message": "错误码:6600145 原因:无该类目权限",
            "payload": complete_payload,
        }
        for index in range(25)
    ]
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-history-25",
                    "store_id": first_store,
                    "items": history_items,
                },
                {
                    "id": "task-second-store",
                    "store_id": second_store,
                    "items": [
                        {
                            "item_id": "history-second-store",
                            "status": "failed",
                            "error_code": 6600145,
                            "payload": complete_payload,
                        }
                    ],
                },
                {
                    "id": "task-ordinary-failure",
                    "store_id": first_store,
                    "items": [
                        {
                            "item_id": "ordinary-failure",
                            "status": "failed",
                            "message": "普通参数错误",
                            "payload": complete_payload,
                        }
                    ],
                },
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)
    calls = []
    monkeypatch.setenv("LANG_LANG_UPLOAD_CATEGORY_RETRY_SECONDS", "0.05")
    monkeypatch.setattr(
        controller,
        "_start_upload_job",
        lambda payload: calls.append(payload) or {"job_id": "legacy-resume"},
    )

    listed = controller.dispatch("upload.list", {})["tasks"]
    for _ in range(50):
        if calls:
            break
        time.sleep(0.01)

    first_items = next(
        task["items"] for task in listed if task["id"] == "task-history-25"
    )
    assert all(item["status"] == "waiting_category" for item in first_items)
    assert all(item["category_auto_recovery"] for item in first_items)
    assert next(
        task for task in listed if task["id"] == "task-ordinary-failure"
    )["items"][0]["status"] == "failed"
    assert calls
    assert set(calls[0]["task_ids"]) == {
        "task-history-25",
        "task-second-store",
    }
    assert len(calls[0]["item_ids"]) == 26
    assert len(set(calls[0]["item_ids"])) == 26


def test_webui_bootstrap_restores_category_resume_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "webui-bootstrap.db")
    store_id = database.add_store("启动店铺", "wx-bootstrap", "secret")
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "task-bootstrap",
                    "store_id": store_id,
                    "items": [
                        {
                            "item_id": "item-bootstrap",
                            "status": "waiting_category",
                            "category_state": "waiting",
                            "category_ids": ["3001"],
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
    )
    controller = NextUiController(database)
    scheduled = []
    monkeypatch.setattr(
        controller,
        "_schedule_upload_category_resume",
        lambda tasks: scheduled.append(tasks),
    )

    controller.dispatch("bootstrap", {})

    assert scheduled
    assert scheduled[0][0]["items"][0]["item_id"] == "item-bootstrap"


def test_webui_category_resume_does_not_busy_loop_during_active_upload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "webui-active.db")
    store_id = database.add_store("类目店铺", "wx-active", "secret")
    tasks = [
        {
            "id": "task-active",
            "store_id": store_id,
            "store_name": "类目店铺",
            "status": "needs_category",
            "items": [
                {
                    "item_id": "item-active",
                    "status": "waiting_category",
                    "category_next_check_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                }
            ],
        }
    ]
    controller = NextUiController(database)
    calls = []
    monkeypatch.setenv(
        "LANG_LANG_UPLOAD_CATEGORY_ACTIVE_RETRY_SECONDS",
        "0.05",
    )
    monkeypatch.setattr(
        controller,
        "_active_upload_job",
        lambda _kind: {"status": "running"},
    )
    monkeypatch.setattr(
        controller,
        "_start_upload_job",
        lambda payload: calls.append(payload) or {"job_id": "unexpected"},
    )

    controller._schedule_upload_category_resume(tasks)
    timer = controller._upload_category_timer
    time.sleep(0.2)
    assert calls == []
    assert timer is not None
    timer.cancel()


def test_webui_template_forces_category_auto_apply(tmp_path: Path) -> None:
    controller = NextUiController(Database(tmp_path / "webui.db"))

    saved = controller.dispatch(
        "upload.templates.save",
        {
            "template": {
                "name": "旧模板 false",
                "auto_open_category": False,
                "category_mode": "manual",
            }
        },
    )["template"]

    assert saved["auto_open_category"] is True
    assert saved["category_mode"] == "auto"


def test_webui_does_not_expose_1688_link_parse_or_collection_rpcs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    for action in (
        "upload.links.preview",
        "upload.links.collect.start",
        "upload.links.collect.status",
    ):
        with pytest.raises(
            ValueError,
            match="不支持的界面操作",
        ):
            controller.dispatch(action, {})


def test_delivery_ledger_reports_real_counts_boundaries_and_navigation(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("交付店铺", "wx-ledger", "secret")
    database.set_store_credential_state(
        store_id,
        "invalid",
        error_code="40125",
        mark_probe=True,
    )
    database.create_store_credential_candidate(
        store_id,
        "Candidate-" + "L" * 24,
        source_kind="paste",
        source_name="fixture",
        source_row=2,
    )
    database.set_setting(
        "upload_tasks",
        json.dumps([{"id": "task-ledger", "items": []}]),
    )
    log_id = database.start_task_log(
        "stores",
        "ledger_fixture",
        "交付账本测试日志",
    )
    database.finish_task_log(log_id, "success", "完成")
    context = SimpleNamespace(
        account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
        username="账本账号",
        email="ledger@example.test",
        offline=False,
        organization_name="账本组织",
        member_role="owner",
        permissions={},
        license=None,
        tokens=SimpleNamespace(access_token="ledger-token"),
    )
    controller = NextUiController(
        database,
        auth_context=context,
        auth_client=SimpleNamespace(
            organization_context=lambda _token: {
                "organization_id": "org",
                "role": "owner",
                "permissions": {},
            }
        ),
    )

    payload = controller.dispatch("delivery.ledger", {})
    modules = {row["id"]: row for row in payload["modules"]}
    boundary_ids = {row["id"] for row in payload["boundaries"]}
    more_nav = {
        row["key"]: row
        for row in controller.dispatch("bootstrap", {})["more_nav"]
    }

    assert payload["counts"]["stores"] == 1
    assert payload["counts"]["upload_tasks"] == 1
    assert payload["counts"]["credential_invalid"] == 1
    assert payload["counts"]["credential_candidates_pending"] == 1
    assert modules["orders"]["status"] == "completed"
    assert modules["upload"]["status"] == "partial"
    assert modules["activity"]["status"] == "partial"
    assert modules["activity"]["route"] == "/activity"
    assert modules["customer_service"]["status"] == "not_implemented"
    assert "1688_live" in boundary_ids
    assert "product_create" in boundary_ids
    assert "secret_replace" in boundary_ids
    assert "activity_live" in boundary_ids
    assert payload["recent_audit"][0]["operation"] == "ledger_fixture"
    assert more_nav["runtime_logs"]["path"] == "/more"
    assert more_nav["runtime_logs"]["enabled"] is True
    assert more_nav["big_screen"]["path"] == "/big-screen"
    assert more_nav["big_screen"]["enabled"] is True
    assert more_nav["delivery_ledger"]["path"] == "/more/delivery-ledger"
    assert more_nav["activity"]["enabled"] is True
    assert more_nav["activity"]["path"] == "/activity"
    assert "Candidate-LLLL" not in json.dumps(payload, ensure_ascii=False)


def test_hosted_execute_blocks_unavailable_actions_without_writes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("托管店铺", "wx-hosted", "secret")
    database.update_hosted_config(
        store_id,
        enabled=True,
        enable_invite=True,
    )
    service = FakeProductService()
    controller = NextUiController(database, product_service=service)

    result = controller.dispatch(
        "hosted.execute",
        {"store_ids": [store_id]},
    )

    assert result["succeeded"] == 0
    assert "达人邀约" in result["errors"][0]
    assert service.writes == []
    assert database.list_hosted_runs()[0]["result"] == "failed"


def test_product_refresh_job_reports_live_read_count(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("进度店铺", "wx-product-progress", "secret")

    class ProgressService:
        received_store_ids: list[int] = []

        def refresh_product_tab(self, store, _tab_key, progress=None, **_kwargs):
            self.received_store_ids.append(store.id)
            if progress:
                progress("已获取商品目录", 0, 2)
                progress("正在读取商品详情 1/2", 1, 2)
                progress("正在读取商品详情 2/2", 2, 2)
            return SyncResult("product_query", 2, "商品已读取", success=2)

    controller = NextUiController(database, product_service=ProgressService())
    started = controller.dispatch("products.refresh.start", {"store_id": store_id})
    for _ in range(50):
        status = controller.dispatch(
            "products.refresh.status",
            {"job_id": started["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "completed"
    assert status["current"] == 2
    assert status["total"] == 2
    assert controller.product_service.received_store_ids == [store_id]
