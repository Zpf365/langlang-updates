from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import weimi_manager.db as db_module
from weimi_manager.db import Database
from weimi_manager.service import SyncService
from weimi_manager.shop_private import ShopPrivateClient
from weimi_manager.webui.controller import NextUiController

NOW = datetime(
    2026,
    11,
    27,
    10,
    0,
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


def test_account_balance_keeps_missing_funds_unknown_and_real_zero(
) -> None:
    client = object.__new__(ShopPrivateClient)
    client._get = lambda _path: {
        "data": {
            "availableAmount": 0,
            "pendingAmount": None,
        }
    }

    balance = client.account_balance()

    assert balance == {
        "cash": 0,
        "frozen": None,
        "settle": None,
    }


def test_controller_uses_per_metric_dates_and_never_relabels_recommend_exposure(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "metric-dates.db")
    first_id = database.add_store(
        "日期店铺一",
        "wx-metric-dates-one",
        "secret",
    )
    second_id = database.add_store(
        "日期店铺二",
        "wx-metric-dates-two",
        "secret",
    )
    database.set_setting(
        f"store_metrics_{first_id}",
        json.dumps(
            {
                "_cache_store_id": first_id,
                "_cache_store_app_id": "wx-metric-dates-one",
                "_exposure_attempt_status": {"today": "updated"},
                "_exposure_count_attempt_status": {"today": "updated"},
                "_exposure_people_attempt_status": {"today": "failed"},
                "_exposure_updated_at": "2026-11-25 20:00:00",
                "_exposure_source": "商品数据旧缓存",
                "recommend_exposure": 999,
                "recommend_exposure_count": 999,
                "recommend_exposure_people": 999,
                "_online_product_attempt_status": "updated",
                "_online_product_updated_at": "2026-11-24 18:00:00",
                "_online_product_source": "官方在售商品总数",
                "in_sale": 17,
                "_sales_attempt_status": {"yesterday": "updated"},
                "_sales_period_sources": {
                    "yesterday": "微信小店后台昨日成交统计",
                },
                "_sales_updated_at": "2026-11-26 23:55:00",
                "_sales_snapshot_date": "2026-11-26",
                "gmv_yesterday": 1234,
                "orders_yesterday": 3,
                "cash": 12300,
                "settle": 0,
                "_fund_attempt_status": {
                    "cash": "updated",
                    "settle": "updated",
                },
                "_fund_source": "官方资金账户",
                "_fund_updated_at": "2026-11-26 23:50:00",
            },
            ensure_ascii=False,
        ),
    )
    database.set_setting(
        f"store_metrics_{second_id}",
        json.dumps(
            {
                "_cache_store_id": second_id,
                "_cache_store_app_id": "wx-metric-dates-two",
                "_sales_period_sources": {
                    "yesterday": "微信小店后台昨日成交统计",
                },
                "_sales_snapshot_date": "2026-11-27",
                "gmv_yesterday": 5678,
                "orders_yesterday": 7,
            },
            ensure_ascii=False,
        ),
    )

    listed = {
        row["id"]: row
        for row in NextUiController(
            database,
            now_provider=lambda: NOW,
        )._list_stores({})["stores"]
    }
    first = listed[first_id]
    second = listed[second_id]

    assert first["as_of_date"] == "2026-11-26"
    assert first["exposure_meta"]["as_of_date"] == "2026-11-25"
    assert first["product_exposure_people"]["today"] is None
    assert first["product_exposure_counts"]["today"] is None
    assert first["exposure_people"]["today"] is None
    assert first["exposure_metrics"]["today"] is None
    assert first["online_products_meta"]["as_of_date"] == "2026-11-24"
    assert first["sales_meta"]["as_of_date"] == "2026-11-26"
    assert first["metrics"]["gmv_yesterday"] == 1234
    assert first["metrics"]["orders_yesterday"] == 3
    assert first["fund_meta"]["as_of_date"] == "2026-11-26"
    assert first["fund_meta"]["source"] == "官方资金账户"
    assert second["metrics"]["gmv_yesterday"] == 5678
    assert second["metrics"]["orders_yesterday"] == 7
    assert second["metrics"].get("cash") is None


def test_controller_serializes_today_new_products_and_yesterday_sales_from_real_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = Database(tmp_path / "new-and-yesterday.db")
    store_id = database.add_store(
        "真实序列化店铺",
        "wx-real-serialization",
        "secret",
    )
    _complete_catalog_scan(
        database,
        store_id,
        ["new-product"],
        monkeypatch,
        "2026-11-27T01:00:00+00:00",
    )
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "_cache_store_id": store_id,
                "_cache_store_app_id": "wx-real-serialization",
                "_sales_attempt_status": {"yesterday": "updated"},
                "_sales_period_sources": {
                    "yesterday": "微信小店后台昨日成交统计",
                },
                "_sales_updated_at": "2026-11-27 09:30:00",
                "gmv_yesterday": 4321,
                "orders_yesterday": 6,
            },
            ensure_ascii=False,
        ),
    )

    row = NextUiController(
        database,
        now_provider=lambda: NOW,
    )._list_stores({})["stores"][0]

    assert row["new_products_today"] == 1
    assert row["new_products_today_meta"]["scan_completed_today"] is True
    assert row["new_products_today_meta"]["as_of_date"] == "2026-11-27"
    assert row["metrics"]["gmv_yesterday"] == 4321
    assert row["metrics"]["orders_yesterday"] == 6
    assert row["sales_meta"]["as_of_date"] == "2026-11-27"


def test_store_metric_failure_preserves_last_trustworthy_values_and_error_context(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "metric-failure.db")
    store_id = database.add_store(
        "失败保护店铺",
        "wx-metric-failure",
        "secret",
    )
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "_cache_store_id": store_id,
                "_cache_store_app_id": "wx-metric-failure",
                "product_exposure_today": 71,
                "product_exposure_count_today": 120,
                "_exposure_attempt_status": {"today": "updated"},
                "_exposure_count_attempt_status": {"today": "updated"},
                "_exposure_people_attempt_status": {"today": "updated"},
                "_exposure_period_sources": {"today": "商品数据旧缓存"},
                "_exposure_source": "商品数据旧缓存",
                "_exposure_updated_at": "2026-11-26 08:00:00",
                "in_sale": 17,
                "_online_product_attempt_status": "updated",
                "_online_product_source": "官方在售商品总数",
                "_online_product_updated_at": "2026-11-26 08:05:00",
                "cash": 12300,
                "frozen": 500,
                "settle": 45600,
                "_fund_attempt_status": {
                    "cash": "updated",
                    "frozen": "updated",
                    "settle": "updated",
                },
                "_fund_source": "资金账户旧缓存",
                "_fund_updated_at": "2026-11-26 08:10:00",
            },
            ensure_ascii=False,
        ),
    )
    store = database.get_store(store_id)
    assert store is not None

    class FailingPrivateClient:
        def realtime_overview(self):
            return {}

        def period_overview(self, days=1, offset_days=1):
            return {}

        def todo_overview(self):
            return {}

        def score_overview(self):
            return {}

        def evaluation_overview(self):
            return {}

        def account_balance(self):
            return {}

        def deposit_balance(self):
            return None

        def product_overview(self, *, days=1, offset_days=0, realtime=False):
            raise RuntimeError("商品曝光接口超时")

        def status_counts(self):
            raise RuntimeError("在线商品接口超时")

        def search_overview(self, _days):
            return {"search_exposure_yesterday": 0}

        def audit_success_today(self):
            return 0

    service = SyncService(database, now_provider=lambda: NOW)
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=FailingPrivateClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["product_exposure_today"] == 71
    assert metrics["product_exposure_count_today"] == 120
    assert metrics["_exposure_attempt_status"]["today"] == "stale"
    assert metrics["_exposure_period_updated_at"]["today"] == "2026-11-26 08:00:00"
    assert "商品曝光接口超时" in metrics["_exposure_error"]
    assert metrics["in_sale"] == 17
    assert metrics["_online_product_attempt_status"] == "stale"
    assert metrics["_online_product_updated_at"] == "2026-11-26 08:05:00"
    assert "在线商品接口超时" in metrics["_online_product_error"]
    assert metrics["cash"] == 12300
    assert metrics["frozen"] == 500
    assert metrics["settle"] == 45600
    assert metrics["_fund_attempt_status"] == {
        "cash": "stale",
        "frozen": "stale",
        "settle": "stale",
    }
    assert metrics["_fund_updated_at"] == "2026-11-26 08:10:00"
    assert "资金" in metrics["_fund_error"]
