from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import weimi_manager.db as db_module
from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController

YESTERDAY = "2026-11-21T02:00:00+00:00"
TODAY = "2026-11-22T02:00:00+00:00"
NOW = datetime(
    2026,
    11,
    22,
    12,
    0,
    tzinfo=timezone(timedelta(hours=8), name="Asia/Shanghai"),
)


def _complete_catalog_scan(
    database: Database,
    store_id: int,
    product_ids: list[str],
    monkeypatch,
    timestamp: str,
) -> None:
    monkeypatch.setattr(
        db_module,
        "_now",
        lambda stamp=timestamp: stamp,
        raising=True,
    )
    run_id = database.start_sync_run(store_id, "product_catalog")
    scan = database.begin_product_scan(store_id, f"scan-{store_id}-{timestamp}")
    database.save_product_scan_page(
        store_id,
        str(scan["scan_id"]),
        product_ids,
        "",
    )
    database.complete_product_scan(store_id, str(scan["scan_id"]))
    database.finish_sync_run(run_id, "success", len(product_ids), "ok")


def test_new_products_today_is_observational_scoped_and_scan_aware(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "new-products.db")
    store_a = database.add_store("店铺 A", "wx-new-products-a", "secret")
    store_b = database.add_store("店铺 B", "wx-new-products-b", "secret")
    unscanned = database.add_store("未扫描店铺", "wx-new-products-unscanned", "secret")
    no_new = database.add_store("无新增店铺", "wx-new-products-none", "secret")

    _complete_catalog_scan(
        database,
        store_a,
        ["old-product"],
        monkeypatch,
        YESTERDAY,
    )
    _complete_catalog_scan(
        database,
        store_a,
        ["new-product-1", "new-product-2", "new-product-1"],
        monkeypatch,
        TODAY,
    )
    _complete_catalog_scan(
        database,
        store_b,
        ["shared-product-id"],
        monkeypatch,
        TODAY,
    )
    _complete_catalog_scan(
        database,
        no_new,
        ["old-product"],
        monkeypatch,
        YESTERDAY,
    )
    _complete_catalog_scan(
        database,
        no_new,
        ["old-product"],
        monkeypatch,
        TODAY,
    )

    result = database.product_new_today_map(
        {store_a, store_b, unscanned, no_new},
        now=NOW,
    )

    assert result[store_a]["count"] == 2
    assert result[store_b]["count"] == 1
    assert result[unscanned] == {
        "count": None,
        "scan_completed_today": False,
        "last_scan_completed_at": "",
    }
    assert result[no_new]["count"] == 0
    assert result[no_new]["scan_completed_today"] is True

    database.set_setting(
        f"store_metrics_{store_a}",
        json.dumps(
            {
                "recommend_exposure": 1234,
                "recommend_exposure_yesterday": 0,
                "recommend_exposure_7d": 9876,
                "recommend_exposure_count": 21084,
                "recommend_exposure_yesterday_count": 0,
                "recommend_exposure_7d_count": 150000,
                "recommend_exposure_people": 16754,
                "recommend_exposure_yesterday_people": 0,
                "recommend_exposure_7d_people": 125600,
                    "product_exposure_today": 16754,
                    "product_exposure_yesterday": 0,
                    "product_exposure_7d": 125600,
                    "product_exposure_count_today": 21084,
                    "product_exposure_count_yesterday": 0,
                    "product_exposure_count_7d": 150000,
                    "product_click_people_today": 321,
                    "product_click_people_yesterday": 0,
                    "product_click_people_7d": 2400,
                    "product_click_count_today": 500,
                    "product_click_count_yesterday": 0,
                    "product_click_count_7d": 3800,
                "_exposure_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                },
                "_exposure_count_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                },
                "_exposure_people_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                },
                "_exposure_source": "官方曝光接口",
                    "_exposure_updated_at": "2026-11-22 17:50:00",
                "gmv_today": 13458,
                "orders_today": 8,
                "buyers_today": 4,
                "_sales_attempt_status": {
                    "today": "updated",
                    "yesterday": "failed",
                    "seven_days": "updated",
                    "thirty_days": "updated",
                },
                "_sales_period_sources": {
                    "today": "微信小店实时成交统计",
                    "yesterday": "微信小店后台昨日成交统计",
                    "seven_days": "微信小店后台近7天成交统计",
                    "thirty_days": "微信小店后台近30天成交统计",
                },
                "_sales_source": "微信小店实时成交统计",
                "_sales_updated_at": "2026-11-22 17:50:41",
                "_sales_is_stale": True,
                "_sales_error": "yesterday: timeout",
                "in_sale": 321,
                "_online_product_attempt_status": "updated",
                "_online_product_source": "官方在售商品总数",
                    "_online_product_updated_at": "2026-11-22 17:49:00",
            },
            ensure_ascii=False,
        ),
    )
    listed = {
        store["id"]: store
        for store in NextUiController(
            database,
            now_provider=lambda: NOW,
        )._list_stores({})["stores"]
    }
    row = listed[store_a]
    assert row["exposure_metrics"] == {
        "today": 16754,
        "yesterday": 0,
        "seven_days": 125600,
    }
    assert row["exposure_counts"] == {
        "today": 21084,
        "yesterday": 0,
        "seven_days": 150000,
    }
    assert row["exposure_people"] == {
        "today": 16754,
        "yesterday": 0,
        "seven_days": 125600,
    }
    assert row["online_products"] == 321
    assert row["online_products_meta"]["as_of_date"] == "2026-11-22"
    assert row["metrics"]["gmv_today"] == 13458
    assert row["as_of_date"] == "2026-11-22"
    assert row["sales_meta"] == {
        "status": {
            "today": "updated",
            "yesterday": "failed",
            "seven_days": "updated",
            "thirty_days": "updated",
        },
        "sources": {
            "today": "微信小店实时成交统计",
            "yesterday": "微信小店后台昨日成交统计",
            "seven_days": "微信小店后台近7天成交统计",
            "thirty_days": "微信小店后台近30天成交统计",
        },
        "source": "微信小店实时成交统计",
        "updated_at": "2026-11-22 17:50:41",
        "stale": True,
        "as_of_date": "2026-11-22",
        "period_dates": {},
        "gift_exclusion": {},
        "refund_amounts": {
            "status": {},
            "sources": {},
            "updated_at": "",
            "error": "",
            "as_of_date": "",
            "period_dates": {},
        },
        "refund_order_counts": {},
        "refund_order_cache": {},
        "error": "yesterday: timeout",
    }
    assert row["new_products_today"] == 2
    assert row["new_products_today_meta"]["timezone"] == "Asia/Shanghai"
    assert row["new_products_today_meta"]["as_of_date"] == "2026-11-22"
    assert listed[unscanned]["new_products_today"] is None
    assert listed[no_new]["new_products_today"] == 0


def test_new_products_do_not_reappear_or_disappear_with_catalog_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "new-products-stability.db")
    store_id = database.add_store("稳定店铺", "wx-new-products-stable", "secret")

    _complete_catalog_scan(
        database,
        store_id,
        ["product-1"],
        monkeypatch,
        TODAY,
    )
    first = database.product_new_today_map({store_id}, now=NOW)[store_id]["count"]
    database.remove_product_cache(store_id, "product-1")
    second = database.product_new_today_map({store_id}, now=NOW)[store_id]["count"]

    assert first == 1
    assert second == 1
