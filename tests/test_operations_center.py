from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.auth_client import AuthError
from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem, Product
from weimi_manager.webui.controller import NextUiController


class PermissionClient:
    def __init__(self, role: str, permissions: dict[str, bool]) -> None:
        self.role = role
        self.permissions = permissions
        self.calls = 0

    def organization_context(self, _access_token: str) -> dict:
        self.calls += 1
        return {
            "organization_id": "org-test",
            "role": self.role,
            "permissions": dict(self.permissions),
        }


def auth_context() -> SimpleNamespace:
    return SimpleNamespace(
        account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
        username="zpfly",
        offline=False,
        member_role="owner",
        permissions={},
        tokens=SimpleNamespace(access_token="test-token"),
    )


def controller(
    database: Database,
    *,
    role: str = "owner",
    permissions: dict[str, bool] | None = None,
    web_session=None,
) -> NextUiController:
    client = PermissionClient(role, permissions or {})
    return NextUiController(
        database,
        auth_context=auth_context(),
        auth_client=client,
        web_session=web_session,
    )


def test_dashboard_keeps_missing_metrics_null_and_scores_are_five_point(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("运营店铺", "wx-ops-one", "secret")
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "dsr": 4.62,
                "score_product": 4.4,
                "score_deliver": 4.7,
                "score_service": 4.8,
                "stats_at": "2026-11-21 10:00:00",
            },
            ensure_ascii=False,
        ),
    )
    missing_id = database.add_store("缺数店铺", "wx-ops-two", "secret")
    database.set_setting(
        f"store_metrics_{missing_id}",
        json.dumps({"stats_at": "2026-11-21 10:00:00"}),
    )

    result = controller(database).dispatch("operations.dashboard", {})
    rows = {row["store_id"]: row for row in result["stores"]}

    assert rows[store_id]["scores"]["dsr"] == 4.62
    assert rows[store_id]["quality_refund_ratio"] is None
    assert rows[missing_id]["scores"]["dsr"] is None
    assert rows[missing_id]["exposure_today"] is None


def test_dashboard_source_errors_redact_secret_material(tmp_path: Path) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("错误店铺", "wx-error", "secret")
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "_errors": [
                    "invalid appsecret rid: abc123 secret-value",
                    "access token: token-value",
                ]
            }
        ),
    )

    result = controller(database).dispatch("operations.dashboard", {})
    rendered = json.dumps(result, ensure_ascii=False)

    assert "abc123" not in rendered
    assert "token-value" not in rendered
    assert "[已隐藏]" in rendered


def test_product_rows_and_detail_use_real_price_stock_and_sold_fields(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("商品字段店铺", "wx-product-fields", "secret")
    database.upsert_product(
        Product(
            store_id=store_id,
            store_name="商品字段店铺",
            product_id="product-real-fields",
            title="真实商品",
            status=5,
            status_text="上架",
            edit_status=0,
            min_price=2999,
            sku_count=2,
            stock_total=37,
            spu_code="SPU-1",
            out_product_id="",
            head_img="",
            raw={"total_sold_num": 18},
        )
    )
    operations = controller(database)

    listed = operations.dispatch("operations.products", {})["products"][0]
    detail = operations.dispatch(
        "operations.product-detail",
        {"store_id": store_id, "product_id": "product-real-fields"},
    )["product"]

    assert listed["price"] == 2999
    assert listed["stock"] == 37
    assert listed["sales"] == 18
    assert detail["price"] == 2999
    assert detail["stock"] == 37
    assert detail["sales"] == 18


def test_product_detail_clusters_only_matching_store_and_product_after_sales(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    first_store = database.add_store("售后隔离店铺", "wx-after-sale-one", "secret")
    second_store = database.add_store("其他店铺", "wx-after-sale-two", "secret")
    target_product = "product-after-sale-target"
    other_product = "product-after-sale-other"

    def add_order(store_id: int, store_name: str, order_id: str, product_id: str) -> None:
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name=store_name,
                order_id=order_id,
                status=20,
                status_text="已发货",
                create_time=1_763_000_000,
                update_time=1_763_000_100,
                pay_time=1_763_000_050,
                total_amount=2999,
                product_amount=2999,
                freight_amount=0,
                discount_amount=0,
                buyer_name="买家",
                phone="",
                address="",
                waybill_id="WB1",
                carrier="快递",
                merchant_notes="",
                items=[
                    OrderItem(
                        product_id=product_id,
                        sku_id=f"sku-{product_id}",
                        title="商品",
                        sku_count=1,
                        sale_price=2999,
                    )
                ],
            )
        )

    for store_id, store_name, product_id in (
        (first_store, "售后隔离店铺", target_product),
        (first_store, "售后隔离店铺", other_product),
        (second_store, "其他店铺", target_product),
    ):
        database.upsert_product(
            Product(
                store_id=store_id,
                store_name=store_name,
                product_id=product_id,
                title=f"{store_name}-{product_id}",
                status=5,
                status_text="上架",
                edit_status=0,
                min_price=2999,
                sku_count=1,
                stock_total=10,
                spu_code="",
                out_product_id="",
                head_img="",
                raw={"total_sold_num": 1},
            )
        )
    add_order(first_store, "售后隔离店铺", "order-target", target_product)
    add_order(first_store, "售后隔离店铺", "order-other", other_product)
    add_order(second_store, "其他店铺", "order-other-store", target_product)
    for after_sale_id, order_id, reason, update_time in (
        ("AS-target-1", "order-target", "商品破损", 1_763_000_300),
        ("AS-target-2", "order-target", "商品破损", 1_763_000_500),
    ):
        database.upsert_after_sale(
            first_store,
            {
                "after_sale_order_id": after_sale_id,
                "order_id": order_id,
                "status": "MERCHANT_PROCESSING",
                "type": "退货退款",
                "reason_text": reason,
                "refund_info": {"amount": 2999},
                "create_time": update_time - 100,
                "update_time": update_time,
            },
        )
    database.upsert_after_sale(
        first_store,
        {
            "after_sale_order_id": "AS-other-product",
            "order_id": "order-other",
            "reason_text": "尺寸不符",
        },
    )
    database.upsert_after_sale(
        second_store,
        {
            "after_sale_order_id": "AS-other-store",
            "order_id": "order-other-store",
            "reason_text": "颜色不符",
        },
    )
    database.set_setting(
        f"store_metrics_{first_store}",
        json.dumps(
            {
                "good_eval_ratio": 96.5,
                "quality_refund_ratio": 2.2,
                "stats_at": "2026-11-21 10:00:00",
            }
        ),
    )

    detail = controller(database).dispatch(
        "operations.product-detail",
        {"store_id": first_store, "product_id": target_product},
    )
    listed = controller(database).dispatch(
        "operations.products",
        {"store_id": first_store},
    )["products"]
    listed_target = next(
        row for row in listed if row["product_id"] == target_product
    )

    assert [row["after_sale_order_id"] for row in detail["after_sales"]] == [
        "AS-target-2",
        "AS-target-1",
    ]
    assert listed_target["after_sale_count"] == 2
    assert detail["after_sale_reason_clusters"] == [
        {"reason": "商品破损", "count": 2}
    ]
    assert detail["evaluation_overview"]["good_eval_ratio"] == 96.5
    assert "raw_json" not in detail["after_sales"][0]
    assert any("品质" in item for item in detail["after_sale_advice"])
    assert any("评价明细尚未接入" in item for item in detail["after_sale_advice"])


def test_product_sales_momentum_uses_unique_paid_orders_and_store_product_isolation(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    first_store = database.add_store("升温店铺", "wx-momentum-one", "secret")
    second_store = database.add_store("隔离店铺", "wx-momentum-two", "secret")
    product_ids = (
        "product-today-two",
        "product-consecutive-three",
        "product-interrupted",
        "product-cancelled",
        "product-duplicate-lines",
    )
    now = datetime.now()

    def timestamp_for(days_ago: int) -> int:
        target = (now - timedelta(days=days_ago)).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        )
        return int(target.timestamp())

    def add_product(store_id: int, store_name: str, product_id: str) -> None:
        database.upsert_product(
            Product(
                store_id=store_id,
                store_name=store_name,
                product_id=product_id,
                title=f"{store_name}-{product_id}",
                status=5,
                status_text="上架",
                edit_status=0,
                min_price=1999,
                sku_count=1,
                stock_total=100,
                spu_code="",
                out_product_id="",
                head_img="",
                raw={"total_sold_num": 0},
            )
        )

    def add_order(
        store_id: int,
        store_name: str,
        order_id: str,
        product_id: str,
        days_ago: int,
        *,
        status: int = 20,
        duplicate_lines: int = 1,
    ) -> None:
        created = timestamp_for(days_ago)
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name=store_name,
                order_id=order_id,
                status=status,
                status_text="有效订单" if status in {17, 20, 21, 30, 100} else "无效订单",
                create_time=created,
                update_time=created,
                pay_time=created,
                total_amount=1999 * duplicate_lines,
                product_amount=1999 * duplicate_lines,
                freight_amount=0,
                discount_amount=0,
                buyer_name="买家",
                phone="",
                address="",
                waybill_id="",
                carrier="",
                merchant_notes="",
                items=[
                    OrderItem(
                        product_id=product_id,
                        sku_id=f"sku-{product_id}-{index}",
                        title="商品",
                        sku_count=1,
                        sale_price=1999,
                    )
                    for index in range(duplicate_lines)
                ],
            )
        )

    for product_id in (*product_ids, "product-store-isolation"):
        add_product(first_store, "升温店铺", product_id)
    add_product(second_store, "隔离店铺", "product-store-isolation")

    add_order(first_store, "升温店铺", "today-a1", "product-today-two", 0)
    add_order(first_store, "升温店铺", "today-a2", "product-today-two", 0)
    for days_ago in (0, 1, 2):
        add_order(
            first_store,
            "升温店铺",
            f"consecutive-{days_ago}",
            "product-consecutive-three",
            days_ago,
        )
    add_order(
        first_store,
        "升温店铺",
        "interrupted-yesterday",
        "product-interrupted",
        1,
    )
    add_order(
        first_store,
        "升温店铺",
        "interrupted-gap",
        "product-interrupted",
        3,
    )
    add_order(
        first_store,
        "升温店铺",
        "cancelled-today",
        "product-cancelled",
        0,
        status=250,
    )
    add_order(
        first_store,
        "升温店铺",
        "refunded-today",
        "product-cancelled",
        0,
        status=200,
    )
    add_order(
        first_store,
        "升温店铺",
        "duplicate-lines",
        "product-duplicate-lines",
        0,
        duplicate_lines=3,
    )
    add_order(
        first_store,
        "升温店铺",
        "store-one-isolation",
        "product-store-isolation",
        0,
    )
    add_order(
        second_store,
        "隔离店铺",
        "store-two-isolation",
        "product-store-isolation",
        0,
    )

    rows = {
        (row["store_id"], row["product_id"]): row
        for row in controller(database).dispatch("operations.products", {})["products"]
    }

    today_two = rows[(first_store, "product-today-two")]
    assert today_two["orders_today"] == 2
    assert "sales_momentum" in today_two["opportunity_categories"]
    consecutive = rows[(first_store, "product-consecutive-three")]
    assert consecutive["consecutive_order_days"] == 3
    assert "sales_momentum" in consecutive["opportunity_categories"]
    interrupted = rows[(first_store, "product-interrupted")]
    assert interrupted["orders_today"] == 0
    assert interrupted["consecutive_order_days"] == 1
    assert "sales_momentum" not in interrupted["opportunity_categories"]
    cancelled = rows[(first_store, "product-cancelled")]
    assert cancelled["orders_today"] == 0
    assert "sales_momentum" not in cancelled["opportunity_categories"]
    duplicate = rows[(first_store, "product-duplicate-lines")]
    assert duplicate["orders_today"] == 1
    assert "sales_momentum" not in duplicate["opportunity_categories"]
    first_isolation = rows[(first_store, "product-store-isolation")]
    second_isolation = rows[(second_store, "product-store-isolation")]
    assert first_isolation["orders_today"] == 1
    assert second_isolation["orders_today"] == 1
    assert first_isolation["orders_7d"] == 1
    assert second_isolation["orders_7d"] == 1


def test_dashboard_conversion_is_globally_weighted_across_analytics(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    first_store = database.add_store("大流量店铺", "wx-volume-large", "secret")
    second_store = database.add_store("小流量店铺", "wx-volume-small", "secret")
    business_date = datetime.now().date().isoformat()
    database.upsert_analytics_day(
        first_store,
        business_date,
        {"product_click_uv": 1000, "pay_uv": 100},
    )
    database.upsert_analytics_day(
        second_store,
        business_date,
        {"product_click_uv": 19, "pay_uv": 9},
    )

    summary = controller(database).dispatch(
        "operations.dashboard",
        {},
    )["summary"]

    assert summary["click_uv_30d"] == 1019
    assert summary["pay_uv_30d"] == 109
    assert summary["conversion_30d"] == 10.7


def test_no_new_product_observation_uses_catalog_first_seen_history(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    missing_history = database.add_store(
        "无观测历史",
        "wx-no-observation",
        "secret",
    )
    stale_store = database.add_store(
        "连续未上新",
        "wx-stale-catalog",
        "secret",
    )
    fresh_store = database.add_store(
        "最近上新",
        "wx-fresh-catalog",
        "secret",
    )
    now = datetime.now()

    def add_catalog(store_id: int, product_id: str, days_ago: int) -> None:
        seen_at = (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        with database.connect() as conn:
            conn.execute(
                """
                INSERT INTO product_catalog(
                    store_id, product_id, state, attempts, last_error,
                    first_seen_at, last_seen_at, detail_synced_at, last_scan_id
                ) VALUES (?, ?, 'done', 0, '', ?, ?, ?, 'test')
                """,
                (store_id, product_id, seen_at, seen_at, seen_at),
            )

    add_catalog(stale_store, "old-product", 5)
    add_catalog(fresh_store, "old-product", 20)
    add_catalog(fresh_store, "new-product", 0)
    result = controller(database).dispatch("operations.dashboard", {})
    rows = {row["store_id"]: row for row in result["stores"]}

    assert rows[missing_history]["no_new_product_days"] is None
    assert rows[missing_history]["latest_new_product_observed_at"] is None
    assert rows[stale_store]["no_new_product_days"] == 5
    assert rows[fresh_store]["no_new_product_days"] == 0
    stale_alerts = [
        alert
        for alert in result["alerts"]
        if alert["store_id"] == stale_store
        and "未观察到新商品" in alert["title"]
    ]
    assert stale_alerts
    assert any(
        "本地最近首次观测" in evidence
        for evidence in stale_alerts[0]["evidence"]
    )
    detail = controller(database).dispatch(
        "operations.store-detail",
        {"store_id": stale_store},
    )["store"]
    assert detail["no_new_product_days"] == 5
    assert detail["latest_new_product_observed_at"]


def test_store_detail_explains_logistics_score_and_dilution_targets(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("物流诊断店铺", "wx-logistics-risk", "secret")
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "score_deliver": 4.28,
                "express_err": 2,
                "wait_send": 1,
                "wait_receive": 3,
                "stats_at": "2026-11-21 17:00:00",
            },
            ensure_ascii=False,
        ),
    )
    now = datetime.now()
    for index in range(23):
        created = int((now - timedelta(days=index % 10)).timestamp())
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name="物流诊断店铺",
                order_id=f"LOGISTICS-{index}",
                status=20,
                status_text="待发货",
                create_time=created,
                update_time=created,
                pay_time=created,
                total_amount=1000,
                product_amount=1000,
                freight_amount=0,
                discount_amount=0,
                buyer_name="买家",
                phone="",
                address="",
                waybill_id=f"YT{index:08d}",
                carrier="圆通",
                merchant_notes="",
                items=[
                    OrderItem(
                        product_id="P-LOGISTICS",
                        sku_id="SKU-1",
                        title="物流商品",
                        sku_count=1,
                        sale_price=1000,
                    )
                ],
            )
        )
    database.upsert_after_sale(
        store_id,
        {
            "after_sale_order_id": "AS-LOGISTICS",
            "order_id": "LOGISTICS-1",
            "status": 10,
            "type": "refund",
            "reason_text": "物流太慢，未按时送达",
            "refund_info": {"amount": 1000},
            "create_time": int(now.timestamp()),
            "update_time": int(now.timestamp()),
        },
    )

    detail = controller(database).dispatch(
        "operations.store-detail",
        {"store_id": store_id},
    )
    logistics = detail["logistics"]

    assert logistics["score"] == 4.28
    evidence_codes = {row["code"] for row in logistics["evidence"]}
    assert {
        "express_error",
        "wait_send",
        "wait_receive",
        "logistics_after_sale",
    }.issubset(evidence_codes)
    estimates = {
        row["target"]: row["new_full_score_orders"]
        for row in logistics["dilution"]
    }
    assert estimates == {4.5: 11, 4.6: 19, 4.7: 33}
    assert logistics["assumption"].endswith("仅用于运营测算，不代表平台最终分数公式。")


def test_multi_store_search_accepts_space_separated_terms(tmp_path: Path) -> None:
    database = Database(tmp_path / "operations.db")
    database.add_store("杭州一号", "wx-hz-one", "secret")
    database.add_store("上海二号", "wx-sh-two", "secret")
    database.add_store("深圳三号", "wx-sz-three", "secret")

    result = controller(database).dispatch(
        "operations.dashboard",
        {"search": "杭州 上海"},
    )

    assert {row["store_name"] for row in result["stores"]} == {"杭州一号", "上海二号"}


def test_operation_tasks_persist_create_complete_and_delay(tmp_path: Path) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("任务店铺", "wx-task-store", "secret")
    task_controller = controller(database)

    created = task_controller.dispatch(
        "operations.tasks.create",
        {
            "store_id": store_id,
            "title": "核查售后原因",
            "detail": "检查真实售后记录",
            "priority": "P0",
            "category": "售后",
            "evidence_json": {"after_sale_count": 3},
        },
    )["task"]
    completed = task_controller.dispatch(
        "operations.tasks.update",
        {"task_id": created["id"], "status": "done"},
    )["task"]
    delayed = task_controller.dispatch(
        "operations.tasks.update",
        {
            "task_id": created["id"],
            "status": "delayed",
            "due_at": "2026-11-25T18:00:00",
        },
    )["task"]
    listed = task_controller.dispatch("operations.tasks.list", {})["tasks"]

    assert created["status"] == "open"
    assert created["created_by"] == "owner"
    assert completed["completed_at"]
    assert completed["completed_by"] == "owner"
    assert delayed["status"] == "delayed"
    assert delayed["due_at"] == "2026-11-25T18:00:00"
    assert listed[0]["title"] == "核查售后原因"


def test_operations_permissions_fail_closed_without_view_and_revoke_on_next_call(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    denied = controller(database, role="operator", permissions={})

    with pytest.raises(AuthError) as raised:
        denied.dispatch("operations.dashboard", {})
    assert raised.value.status == 403
    assert "权限" in str(raised.value)

    allowed_client = PermissionClient(
        "operator",
        {"operations_view": True},
    )
    allowed = NextUiController(
        database,
        auth_context=auth_context(),
        auth_client=allowed_client,
    )
    assert allowed.dispatch("operations.dashboard", {})["summary"]["stores_total"] == 0
    allowed_client.permissions = {}

    with pytest.raises(AuthError) as revoked:
        allowed.dispatch("operations.dashboard", {})
    assert revoked.value.status == 403
    assert allowed_client.calls >= 2


def test_operations_menu_is_hidden_without_permission_and_owner_defaults_all(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    denied = controller(database, role="operator", permissions={}).dispatch(
        "bootstrap",
        {},
    )
    owner = controller(database, role="owner", permissions={}).dispatch(
        "bootstrap",
        {},
    )

    assert "operations" not in {item["key"] for item in denied["more_nav"]}
    assert "operations" in {item["key"] for item in owner["more_nav"]}
    assert all(owner["operations_permissions"].values())


def test_bootstrap_fails_closed_when_permission_service_is_unavailable(
    tmp_path: Path,
) -> None:
    class UnavailableClient:
        def organization_context(self, _access_token):
            raise RuntimeError("network unavailable")

    database = Database(tmp_path / "operations.db")
    operations = NextUiController(
        database,
        auth_context=auth_context(),
        auth_client=UnavailableClient(),
    )

    payload = operations.dispatch("bootstrap", {})

    assert payload["operations_permissions"]["operations_view"] is False
    assert "operations" not in {item["key"] for item in payload["more_nav"]}


def test_export_and_task_capabilities_require_separate_permissions(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    database.add_store("导出店铺", "wx-export", "secret")
    view_only = controller(
        database,
        role="viewer",
        permissions={"operations_view": True},
    )

    with pytest.raises(AuthError) as export_error:
        view_only.dispatch("operations.export", {"scope": "stores"})
    with pytest.raises(AuthError) as task_error:
        view_only.dispatch(
            "operations.tasks.create",
            {"title": "不应创建"},
        )

    assert export_error.value.status == 403
    assert task_error.value.status == 403


def test_rules_are_saved_to_sqlite(tmp_path: Path) -> None:
    database = Database(tmp_path / "operations.db")
    rules = controller(database).dispatch(
        "operations.rules.save",
        {
            "score_drop_threshold": 0.2,
            "zero_exposure_hours": 3,
            "zero_sales_days": 4,
            "no_new_product_days": 5,
            "first_response_seconds": 90,
            "no_reply_rate": 10,
            "quality_refund_watch_rate": 6,
            "high_views_threshold": 800,
            "low_pay_threshold": 3,
        },
    )

    assert rules["rules"]["score_drop_threshold"] == 0.2
    assert database.get_operation_rules()["no_reply_rate"] == "10.0"


def test_inspection_starts_real_store_refresh_job_and_tracks_progress(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("巡检店铺", "wx-inspect", "secret")
    database.update_web_status(store_id, "已登录", "test")

    class RefreshService:
        def __init__(self) -> None:
            self.refreshed: list[int] = []

        def refresh_store_metrics(self, store):
            self.refreshed.append(store.id)
            return {}

    service = RefreshService()
    operations = NextUiController(database, product_service=service)
    started = operations.dispatch(
        "operations.inspect",
        {"search": "巡检店铺"},
    )

    assert started["job_id"]
    deadline = time.time() + 2
    status = {}
    while time.time() < deadline:
        status = operations.dispatch(
            "operations.inspect.status",
            {"job_id": started["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.02)

    assert status["status"] == "completed"
    assert service.refreshed == [store_id]


class FakeBackendSession:
    def __init__(self) -> None:
        self.opened: list[tuple[int, str]] = []
        self.product_opened: list[tuple[int, str, str]] = []
        self.ready = threading.Event()

    def open_official_backend(self, store, url: str) -> None:
        self.opened.append((store.id, url))
        self.ready.set()

    def open_official_product(self, store, product_id: str, *, target: str) -> None:
        self.product_opened.append((store.id, product_id, target))
        self.ready.set()


def test_official_backend_targets_are_mapped_and_not_blocking(tmp_path: Path) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("深链店铺", "wx-deep-link", "secret")
    session = FakeBackendSession()
    operations = controller(database, web_session=session)

    result = operations.dispatch(
        "operations.open-backend",
        {"store_id": store_id, "target": "score"},
    )

    assert result["opening"] is True
    assert session.ready.wait(timeout=1)
    assert session.opened == [
        (store_id, "https://store.weixin.qq.com/shop/setting/rate2")
    ]


def test_product_deep_link_requires_exact_store_product(tmp_path: Path) -> None:
    database = Database(tmp_path / "operations.db")
    store_id = database.add_store("商品店铺", "wx-product-link", "secret")
    session = FakeBackendSession()
    operations = controller(database, web_session=session)

    with pytest.raises(ValueError, match="商品不存在"):
        operations.dispatch(
            "operations.open-backend",
            {
                "store_id": store_id,
                "target": "product",
                "product_id": "missing-product",
            },
        )

    assert session.product_opened == []


class FakeTeamService:
    def __init__(self) -> None:
        self.members = [
            {
                "member_id": "owner-1",
                "username": "主账号",
                "role": "owner",
                "permissions": {},
                "is_active": True,
            },
            {
                "member_id": "member-2",
                "username": "运营员",
                "role": "operator",
                "permissions": {"orders_view": True},
                "is_active": True,
            },
        ]
        self.updates: list[dict] = []

    def list_members(self):
        return self.members

    def update_member(self, member_id, **payload):
        self.updates.append({"member_id": member_id, **payload})

    def create_subaccount(self, **payload):
        return {"member_id": "member-3", **payload}


def test_team_permission_update_is_audited_and_preserves_other_permissions(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    service = FakeTeamService()
    team_controller = NextUiController(
        database,
        auth_context=auth_context(),
        auth_client=PermissionClient("owner", {}),
        team_service=service,
    )

    result = team_controller.dispatch(
        "team.operations.save",
        {
            "member_id": "member-2",
            "permissions": {
                "operations_view": True,
                "operations_task_manage": True,
                "operations_export": False,
                "operations_rule_manage": False,
                "operations_open_backend": False,
            },
        },
    )
    audit = team_controller.dispatch("team.operations.audit", {})["audit"]

    assert result["member"]["permissions"]["operations_view"] is True
    assert service.updates[0]["permissions"]["orders_view"] is True
    assert service.updates[0]["permissions"]["operations_task_manage"] is True
    assert audit
    detail = json.loads(audit[0]["detail"])
    assert detail["target_member_id"] == "member-2"
    assert detail["actor"] == "owner"


def test_team_can_create_restricted_operations_subaccount(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "operations.db")
    service = FakeTeamService()
    team_controller = NextUiController(
        database,
        auth_context=auth_context(),
        auth_client=PermissionClient("owner", {}),
        team_service=service,
    )

    result = team_controller.dispatch(
        "team.operations.create",
        {
            "username": "new-operator",
            "password": "secure-pass",
            "role": "operator",
            "permissions": {
                "operations_view": True,
                "operations_task_manage": True,
            },
        },
    )

    assert result["created"] is True
    assert result["member"]["permissions"]["operations_view"] is True
    assert result["member"]["permissions"]["operations_open_backend"] is False
