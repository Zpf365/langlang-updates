from __future__ import annotations

from datetime import datetime, timedelta

from weimi_manager.db import SHANGHAI_TZ, Database
from weimi_manager.models import Order
from weimi_manager.webui.controller import NextUiController


def _order(
    *,
    store_id: int,
    store_name: str,
    order_id: str,
    status: int,
    status_text: str,
    update_time: int,
    waybill_id: str = "YT123456",
    ship_time: int = 0,
) -> Order:
    return Order(
        store_id=store_id,
        store_name=store_name,
        order_id=order_id,
        status=status,
        status_text=status_text,
        create_time=update_time - 3600,
        update_time=update_time,
        pay_time=update_time - 7200,
        total_amount=1000,
        product_amount=1000,
        freight_amount=0,
        discount_amount=0,
        buyer_name="测试买家",
        phone="",
        address="",
        waybill_id=waybill_id,
        carrier="YTO",
        merchant_notes="",
        items=[],
        raw={
            "order_detail": {
                "delivery_info": {
                    "ship_done_time": ship_time,
                    "delivery_product_info": [
                        {
                            "waybill_id": waybill_id,
                            "delivery_id": "YTO",
                            "delivery_time": ship_time,
                        }
                    ],
                }
            }
        },
    )


def _after_sale(
    database: Database,
    *,
    store_id: int,
    order_id: str,
    after_sale_id: str,
    status: str,
    status_text: str,
    after_sale_type: str,
    update_time: int,
    return_waybill: str = "",
) -> None:
    database.upsert_after_sale(
        store_id,
        {
            "after_sale_order_id": after_sale_id,
            "order_id": order_id,
            "status": status,
            "status_text": status_text,
            "type": after_sale_type,
            "reason_text": "测试原因",
            "refund_amount": 0,
            "create_time": update_time - 60,
            "update_time": update_time,
            "return_info": {
                "waybill_id": return_waybill,
                "delivery_id": "SF" if return_waybill else "",
            },
        },
    )


def test_logistics_monitor_lists_only_real_abnormal_orders(
    tmp_path,
) -> None:
    database = Database(tmp_path / "logistics-monitor.db")
    first_store = database.add_store(
        "物流测试店",
        "wx-logistics-monitor",
        "secret",
    )
    second_store = database.add_store(
        "第二店铺",
        "wx-logistics-second",
        "secret",
    )
    now = datetime(2026, 11, 24, 12, 0, tzinfo=SHANGHAI_TZ)
    now_ts = int(now.timestamp())
    database.upsert_order(
        _order(
            store_id=first_store,
            store_name="物流测试店",
            order_id="RETURNED",
            status=200,
            status_text="售后退款完成",
            update_time=now_ts - 70 * 3600,
        )
    )
    database.upsert_order(
        _order(
            store_id=first_store,
            store_name="物流测试店",
            order_id="AFTER-SALE",
            status=30,
            status_text="待收货",
            update_time=now_ts - 8 * 3600,
            ship_time=now_ts - 12 * 3600,
        )
    )
    database.upsert_order(
        _order(
            store_id=first_store,
            store_name="物流测试店",
            order_id="STAGNANT",
            status=30,
            status_text="待收货",
            update_time=now_ts - 60 * 3600,
            ship_time=now_ts - 64 * 3600,
        )
    )
    database.upsert_order(
        _order(
            store_id=first_store,
            store_name="物流测试店",
            order_id="MOVING",
            status=30,
            status_text="待收货",
            update_time=now_ts - 2 * 3600,
            ship_time=now_ts - 3 * 3600,
        )
    )
    database.upsert_order(
        _order(
            store_id=first_store,
            store_name="物流测试店",
            order_id="WAIT-PICKUP",
            status=20,
            status_text="待发货",
            update_time=now_ts - 3600,
            ship_time=0,
        )
    )
    database.upsert_order(
        _order(
            store_id=first_store,
            store_name="物流测试店",
            order_id="SIGNED",
            status=100,
            status_text="已完成",
            update_time=now_ts - 86400,
        )
    )
    database.upsert_order(
        _order(
            store_id=second_store,
            store_name="第二店铺",
            order_id="SECOND-AFTER-SALE",
            status=30,
            status_text="待收货",
            update_time=now_ts - 3 * 3600,
            ship_time=now_ts - 4 * 3600,
        )
    )
    _after_sale(
        database,
        store_id=first_store,
        order_id="RETURNED",
        after_sale_id="AS-RETURNED",
        status="MERCHANT_RETURN_SUCCESS",
        status_text="退货退款成功",
        after_sale_type="RETURN",
        update_time=now_ts - 70 * 3600,
        return_waybill="SF-RETURN",
    )
    _after_sale(
        database,
        store_id=first_store,
        order_id="AFTER-SALE",
        after_sale_id="AS-OPEN",
        status="MERCHANT_PROCESSING",
        status_text="商家处理中",
        after_sale_type="REFUND",
        update_time=now_ts - 8 * 3600,
    )
    _after_sale(
        database,
        store_id=second_store,
        order_id="SECOND-AFTER-SALE",
        after_sale_id="AS-SECOND",
        status="MERCHANT_PROCESSING",
        status_text="商家处理中",
        after_sale_type="REFUND",
        update_time=now_ts - 3 * 3600,
    )

    controller = NextUiController(database, now_provider=lambda: now)
    result = controller.dispatch("logistics.monitor", {})

    assert result["metrics"] == {
        "in_transit": 5,
        "awaiting_pickup": 1,
        "transporting": 4,
        "returned": 1,
        "stagnant": 1,
        "after_sale": 2,
        "abnormal": 4,
        "signed_7d": 1,
        "today_abnormal": 3,
    }
    rows = {row["order_id"]: row for row in result["rows"]}
    assert set(rows) == {
        "RETURNED",
        "AFTER-SALE",
        "STAGNANT",
        "SECOND-AFTER-SALE",
    }
    assert rows["RETURNED"]["logistics_status"] == "returned"
    assert rows["RETURNED"]["latest_trace"] == "退货退款完成"
    assert rows["AFTER-SALE"]["logistics_status"] == "after_sale"
    assert rows["STAGNANT"]["logistics_status"] == "stagnant"
    assert rows["STAGNANT"]["elapsed_seconds"] >= 48 * 3600
    assert "MOVING" not in rows
    assert "WAIT-PICKUP" not in rows


def test_logistics_monitor_filters_store_and_paginates(tmp_path) -> None:
    database = Database(tmp_path / "logistics-filter.db")
    first_store = database.add_store(
        "筛选店铺",
        "wx-logistics-filter",
        "secret",
    )
    second_store = database.add_store(
        "其他店铺",
        "wx-logistics-other",
        "secret",
    )
    now = datetime(2026, 11, 24, 12, 0, tzinfo=SHANGHAI_TZ)
    now_ts = int(now.timestamp())
    for index in range(3):
        order_id = f"FIRST-{index}"
        database.upsert_order(
            _order(
                store_id=first_store,
                store_name="筛选店铺",
                order_id=order_id,
                status=200,
                status_text="售后退款完成",
                update_time=now_ts - (index + 50) * 3600,
            )
        )
        _after_sale(
            database,
            store_id=first_store,
            order_id=order_id,
            after_sale_id=f"AS-{index}",
            status="MERCHANT_RETURN_SUCCESS",
            status_text="退货退款成功",
            after_sale_type="RETURN",
            update_time=now_ts - (index + 50) * 3600,
            return_waybill=f"SF-{index}",
        )
    database.upsert_order(
        _order(
            store_id=second_store,
            store_name="其他店铺",
            order_id="OTHER-RETURN",
            status=200,
            status_text="售后退款完成",
            update_time=now_ts - 80 * 3600,
        )
    )
    _after_sale(
        database,
        store_id=second_store,
        order_id="OTHER-RETURN",
        after_sale_id="AS-OTHER",
        status="MERCHANT_RETURN_SUCCESS",
        status_text="退货退款成功",
        after_sale_type="RETURN",
        update_time=now_ts - 80 * 3600,
        return_waybill="SF-OTHER",
    )

    controller = NextUiController(database, now_provider=lambda: now)
    filtered = controller.dispatch(
        "logistics.monitor",
        {"store_id": first_store, "page": 2, "page_size": 2},
    )

    assert filtered["total"] == 3
    assert filtered["page"] == 2
    assert filtered["page_count"] == 2
    assert len(filtered["rows"]) == 1
    assert filtered["rows"][0]["store_id"] == first_store
    assert filtered["metrics"]["abnormal"] == 3


def test_logistics_metric_cards_filter_abnormal_rows_and_keep_counts_only(
    tmp_path,
) -> None:
    database = Database(tmp_path / "logistics-card-filter.db")
    store_id = database.add_store(
        "卡片筛选店铺",
        "wx-logistics-card-filter",
        "secret",
    )
    now = datetime(2026, 11, 24, 12, 0, tzinfo=SHANGHAI_TZ)
    now_ts = int(now.timestamp())
    database.upsert_order(
        _order(
            store_id=store_id,
            store_name="卡片筛选店铺",
            order_id="RETURNED-CARD",
            status=200,
            status_text="售后退款完成",
            update_time=now_ts - 50 * 3600,
        )
    )
    database.upsert_order(
        _order(
            store_id=store_id,
            store_name="卡片筛选店铺",
            order_id="AFTER-SALE-CARD",
            status=30,
            status_text="待收货",
            update_time=now_ts - 3 * 3600,
            ship_time=now_ts - 4 * 3600,
        )
    )
    database.upsert_order(
        _order(
            store_id=store_id,
            store_name="卡片筛选店铺",
            order_id="STAGNANT-CARD",
            status=30,
            status_text="待收货",
            update_time=now_ts - 60 * 3600,
            ship_time=now_ts - 64 * 3600,
        )
    )
    _after_sale(
        database,
        store_id=store_id,
        order_id="RETURNED-CARD",
        after_sale_id="AS-RETURNED-CARD",
        status="MERCHANT_RETURN_SUCCESS",
        status_text="退货退款成功",
        after_sale_type="RETURN",
        update_time=now_ts - 50 * 3600,
        return_waybill="SF-CARD-RETURN",
    )
    _after_sale(
        database,
        store_id=store_id,
        order_id="AFTER-SALE-CARD",
        after_sale_id="AS-AFTER-CARD",
        status="MERCHANT_PROCESSING",
        status_text="商家处理中",
        after_sale_type="REFUND",
        update_time=now_ts - 3 * 3600,
    )
    controller = NextUiController(database, now_provider=lambda: now)

    returned = controller.dispatch(
        "logistics.monitor",
        {"metric_filter": "returned"},
    )
    stagnant = controller.dispatch(
        "logistics.monitor",
        {"metric_filter": "stagnant"},
    )
    after_sale = controller.dispatch(
        "logistics.monitor",
        {"metric_filter": "after_sale"},
    )
    today = controller.dispatch(
        "logistics.monitor",
        {"metric_filter": "today_abnormal"},
    )
    transit = controller.dispatch(
        "logistics.monitor",
        {"metric_filter": "in_transit"},
    )
    pickup = controller.dispatch(
        "logistics.monitor",
        {"metric_filter": "awaiting_pickup"},
    )
    signed = controller.dispatch(
        "logistics.monitor",
        {"metric_filter": "signed_7d"},
    )

    assert returned["active_filter"] == "returned"
    assert [row["order_id"] for row in returned["rows"]] == ["RETURNED-CARD"]
    assert [row["order_id"] for row in stagnant["rows"]] == ["STAGNANT-CARD"]
    assert [row["order_id"] for row in after_sale["rows"]] == ["AFTER-SALE-CARD"]
    assert {row["order_id"] for row in today["rows"]} == {
        "AFTER-SALE-CARD",
        "STAGNANT-CARD",
    }
    assert transit["rows"] == []
    assert transit["total"] == 0
    assert "仅做数量统计" in transit["empty_message"]
    assert transit["metrics"]["in_transit"] == 2
    assert "待揽收" in pickup["empty_message"]
    assert "近7天已签收" in signed["empty_message"]
