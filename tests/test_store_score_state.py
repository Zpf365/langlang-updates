from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from weimi_manager.db import Database, SHANGHAI_TZ
from weimi_manager.models import Order
from weimi_manager.service import (
    SyncService,
    SyncResult,
    _refund_order_counts_for_periods,
)
from weimi_manager.shop_private import ShopPrivateClient, ShopPrivateError
from weimi_manager.wechat_api import WechatApiError, WechatShopClient
from weimi_manager.webui.controller import NextUiController, STORE_SCORE_FIELDS


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"depositBalance": 12345}, 12345),
        ({"depositBalance": "12345"}, 12345),
        ({"data": {"depositBalance": 12345}}, 12345),
        ({"data": {"depositBalance": "0"}}, 0),
        ({"requiredDeposit": 999999}, None),
        ({"data": {}}, None),
        ({"data": {"depositBalance": "not-a-number"}}, None),
    ],
)
def test_deposit_balance_unwraps_official_response(
    response: dict,
    expected: int | None,
) -> None:
    client = object.__new__(ShopPrivateClient)
    client._get = lambda _path: response

    assert client.deposit_balance() == expected


def test_recommend_overview_uses_real_seven_day_range() -> None:
    client = object.__new__(ShopPrivateClient)
    captured: dict = {}

    def post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {
            "data": {
                "data": {
                    "total": {
                        "product_view_cnt": "21084",
                        "product_view_uv": "1234",
                        "product_click_uv": 56,
                        "pay_order_cnt": 7,
                        "pay_gmv": 890,
                    }
                }
            }
        }

    client._post = post
    result = client.recommend_overview(days=7)

    payload = captured["payload"]
    assert captured["path"].endswith("getShopRecommendProductsStat")
    assert payload["isToday"] is False
    assert payload["isYesterday"] is False
    assert payload["time_query"]["dateFormat"] == 2
    assert payload["time_query"]["dateType"] == 0
    assert payload["time_query"]["dateRange"] == 7
    assert payload["time_query"]["startMs"] < payload["time_query"]["endMs"]
    assert result == {
        "exposure": 1234,
        "exposure_count": 21084,
        "exposure_people": 1234,
        "click": 56,
        "orders": 7,
        "gmv": 890,
    }


def test_recommend_overview_does_not_turn_missing_fields_into_zero() -> None:
    client = object.__new__(ShopPrivateClient)
    client._post = lambda _path, _payload: {"data": {"data": {"total": {}}}}

    result = client.recommend_overview(False)
    assert result["exposure"] is None
    assert result["exposure_count"] is None
    assert result["exposure_people"] is None


def test_score_overview_preserves_official_daily_history() -> None:
    client = object.__new__(ShopPrivateClient)
    client._post = lambda _path, _payload: {
        "dsrScore": {
            "curDate": "20261123",
            "curValue": 4.63,
            "valueList": [
                {"date": "20261123", "value": 4.63},
                {"date": "20261122", "value": "4.62"},
            ],
        },
        "productScore": {
            "curDate": "20261123",
            "curValue": "4.78",
            "valueList": [
                {"date": "2026-11-23", "value": 4.78},
                {"date": "20261122", "value": 4.73},
            ],
        },
        "deliverScore": {},
        "serviceScore": {
            "curDate": "20261123",
            "curValue": 4.62,
            "valueList": [
                {"date": "20261123", "value": 4.62},
                {"date": "20261122", "value": 4.64},
            ],
        },
    }

    result = client.score_overview()

    assert result["dsr"] == 4.63
    assert result["score_product"] == 4.78
    assert result["score_deliver"] is None
    assert result["score_service"] == 4.62
    assert result["_score_daily_values"] == {
        "dsr": [
            {"date": "2026-11-23", "value": 4.63},
            {"date": "2026-11-22", "value": 4.62},
        ],
        "score_product": [
            {"date": "2026-11-23", "value": 4.78},
            {"date": "2026-11-22", "value": 4.73},
        ],
        "score_service": [
            {"date": "2026-11-23", "value": 4.62},
            {"date": "2026-11-22", "value": 4.64},
        ],
    }


def test_period_overview_uses_offline_endpoint_and_completed_day_range() -> None:
    client = object.__new__(ShopPrivateClient)
    calls = []
    client._post = lambda path, payload: (
        calls.append((path, payload))
        or {
            "code": 0,
            "total": {
                "pay_gmv": "123",
                "pay_order_cnt": "4",
                "pay_uv": "2",
            },
        }
    )

    result = client.period_overview(7, offset_days=1)

    path, payload = calls[0]
    assert "home-overview-shop-offline" in path
    query = payload["time_query"]
    assert query["dateFormat"] == 2
    assert query["dateType"] == 0
    assert query["dateRange"] == 7
    start = datetime.fromtimestamp(query["startMs"] / 1000)
    end = datetime.fromtimestamp(query["endMs"] / 1000)
    assert (end.date() - start.date()).days == 6
    assert end.date() == date.today() - timedelta(days=1)
    assert result["pay_gmv"] == "123"


@pytest.mark.parametrize(
    ("total", "expected_count", "expected_people", "legacy"),
    [
        (
            {"product_view_cnt": "21", "product_view_uv": "17"},
            21,
            17,
            17,
        ),
        ({"product_view_cnt": "21"}, 21, None, 21),
        ({"product_view_uv": "17"}, None, 17, 17),
        ({"product_view_cnt": 0, "product_view_uv": 0}, 0, 0, 0),
        ({}, None, None, None),
    ],
)
def test_recommend_overview_keeps_count_and_people_separate(
    total: dict,
    expected_count: int | None,
    expected_people: int | None,
    legacy: int | None,
) -> None:
    client = object.__new__(ShopPrivateClient)
    client._post = lambda _path, _payload: {
        "data": {"data": {"total": total}}
    }

    result = client.recommend_overview(False)

    assert result["exposure_count"] == expected_count
    assert result["exposure_people"] == expected_people
    assert result["exposure"] == legacy


def test_official_product_total_does_not_default_missing_total_to_zero() -> None:
    client = object.__new__(WechatShopClient)
    client._post_api = lambda _path, _payload: {"data": {}}

    with pytest.raises(WechatApiError, match="total_num"):
        client.fetch_product_total(5)


def _controller(
    tmp_path: Path,
    *,
    now_provider=None,
):
    database = Database(tmp_path / "store-scores.db")
    store_id = database.add_store("趋势店铺", "wx-score-state", "secret")
    return (
        NextUiController(
            database,
            now_provider=now_provider
            or (lambda: datetime(2026, 11, 22, 10, 5, 0)),
        ),
        database,
        store_id,
    )


def test_score_changes_are_independent_stable_and_survive_missing_values(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 11, 21, 23, 50, 0)]
    controller, database, store_id = _controller(
        tmp_path,
        now_provider=lambda: current[0],
    )
    initial = {
        "dsr": 4.60,
        "score_product": 4.60,
        "score_deliver": 4.60,
        "score_service": 4.60,
    }
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(initial),
    )
    controller._record_store_score_baseline({store_id})
    current[0] = datetime(2026, 11, 22, 10, 5, 0)

    first = {
        "dsr": 4.68,
        "score_product": 4.60,
        "score_deliver": 4.53,
        "score_service": 4.60,
        "_score_attempt_status": {
            "dsr": "updated",
            "score_product": "updated",
            "score_deliver": "updated",
            "score_service": "missing",
        },
        "_score_updated_at": "2026-11-22 10:00:00",
        "_score_source": "微信小店统计接口 scoreTypeList 11/12/13/14",
    }
    controller._record_store_score_refresh_result(store_id, first)
    listed = controller.dispatch("stores.list", {})["stores"][0]
    repeated = controller.dispatch("stores.list", {})["stores"][0]

    assert listed["score_changes"]["dsr"]["from"] == 4.60
    assert listed["score_changes"]["dsr"]["to"] == 4.68
    assert listed["score_changes"]["dsr"]["delta"] == 0.08
    assert listed["score_changes"]["score_deliver"]["delta"] == -0.07
    assert "score_product" not in listed["score_changes"]
    assert "score_service" not in listed["score_changes"]
    assert listed["score_refresh"]["score_service"]["status"] == "missing"
    assert repeated["score_changes"] == listed["score_changes"]
    assert repeated["score_refresh"] == listed["score_refresh"]

    missing = {
        **first,
        "_score_attempt_status": {
            "dsr": "failed",
            "score_product": "failed",
            "score_deliver": "missing",
            "score_service": "missing",
        },
    }
    controller._record_store_score_refresh_result(store_id, missing)
    after_missing = controller.dispatch("stores.list", {})["stores"][0]

    assert after_missing["metrics"]["dsr"] == 4.68
    assert after_missing["metrics"]["score_deliver"] == 4.53
    assert after_missing["score_changes"] == listed["score_changes"]
    assert {
        item["status"]
        for item in after_missing["score_refresh"].values()
    } == {"failed", "missing"}

    controller._record_store_score_refresh_failure(
        store_id,
        "店铺刷新失败",
    )
    after_failure = controller.dispatch("stores.list", {})["stores"][0]
    assert after_failure["metrics"]["dsr"] == 4.68
    assert after_failure["score_changes"] == listed["score_changes"]
    assert all(
        item["status"] == "failed"
        for item in after_failure["score_refresh"].values()
    )

    recovered = {
        **missing,
        "score_service": 4.62,
        "_score_attempt_status": {
            "dsr": "updated",
            "score_product": "updated",
            "score_deliver": "updated",
            "score_service": "updated",
        },
    }
    controller._record_store_score_refresh_result(store_id, recovered)
    after_recovery = controller.dispatch("stores.list", {})["stores"][0]

    assert after_recovery["score_changes"]["score_service"]["delta"] == 0.02
    assert after_recovery["score_changes"]["score_service"]["from"] == 4.60
    assert after_recovery["score_changes"]["score_service"]["to"] == 4.62

    unchanged = {
        **recovered,
        "dsr": 4.68,
        "score_deliver": 4.53,
        "score_service": 4.62,
    }
    controller._record_store_score_refresh_result(store_id, unchanged)
    after_unchanged = controller.dispatch("stores.list", {})["stores"][0]

    assert after_unchanged["score_changes"] == after_recovery["score_changes"]

    next_change = {
        **unchanged,
        "dsr": 4.71,
        "_score_updated_at": "2026-11-22 10:04:00",
    }
    controller._record_store_score_refresh_result(store_id, next_change)
    after_next_change = controller.dispatch("stores.list", {})["stores"][0]
    assert after_next_change["score_changes"]["dsr"] == {
        "from": 4.60,
        "to": 4.71,
        "delta": 0.11,
        "updated_at": "2026-11-22 10:04:00",
        "baseline_date": "2026-11-21",
        "comparison_date": "2026-11-22",
    }


def test_score_trend_uses_daily_snapshots_and_rolls_over_at_midnight(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 11, 21, 23, 50, 0)]
    controller, database, store_id = _controller(
        tmp_path,
        now_provider=lambda: current[0],
    )
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps({"dsr": 4.60}),
    )
    controller._record_store_score_baseline({store_id})

    current[0] = datetime(2026, 11, 22, 10, 5, 0)
    controller._record_store_score_refresh_result(
        store_id,
        {
            "dsr": 4.70,
            "_score_updated_at": "2026-11-22 10:00:00",
            "_score_attempt_status": {"dsr": "updated"},
        },
    )
    day_two = controller.dispatch("stores.list", {})["stores"][0]
    assert day_two["score_changes"]["dsr"]["delta"] == 0.10
    assert day_two["score_changes"]["dsr"]["baseline_date"] == "2026-11-21"

    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "dsr": 4.70,
                "_score_updated_at": "2026-11-22 10:00:00",
            }
        ),
    )
    current[0] = datetime(2026, 11, 23, 10, 5, 0)
    before_fetch = controller.dispatch("stores.list", {})["stores"][0]
    assert before_fetch["metrics"]["dsr"] == 4.70
    assert before_fetch["score_changes"] == day_two["score_changes"]

    controller._record_store_score_refresh_result(
        store_id,
        {
            "dsr": 4.70,
            "_score_updated_at": "2026-11-23 10:00:00",
            "_score_attempt_status": {"dsr": "updated"},
        },
    )
    unchanged_day = controller.dispatch("stores.list", {})["stores"][0]
    assert unchanged_day["score_changes"] == {}

    controller._record_store_score_refresh_result(
        store_id,
        {
            "dsr": 4.73,
            "_score_updated_at": "2026-11-23 10:04:00",
            "_score_attempt_status": {"dsr": "updated"},
        },
    )
    changed_day = controller.dispatch("stores.list", {})["stores"][0]
    assert changed_day["score_changes"]["dsr"]["delta"] == 0.03
    assert changed_day["score_changes"]["dsr"]["from"] == 4.70
    assert changed_day["score_changes"]["dsr"]["to"] == 4.73

    stored = json.loads(
        database.get_setting(
            f"webui_store_score_state_{store_id}",
            "{}",
        )
    )
    assert stored["daily"]["2026-11-21"]["dsr"]["value"] == 4.60
    assert stored["daily"]["2026-11-22"]["dsr"]["value"] == 4.70
    assert stored["daily"]["2026-11-23"]["dsr"]["value"] == 4.73


def test_score_trend_uses_latest_two_official_dates(
    tmp_path: Path,
) -> None:
    controller, database, store_id = _controller(
        tmp_path,
        now_provider=lambda: datetime(2026, 11, 24, 10, 5, 0),
    )
    database.set_setting(
        f"webui_store_score_state_{store_id}",
        json.dumps(
            {
                "version": 3,
                "values": {},
                "changes": {},
                "daily": {
                    "2026-11-24": {
                        "dsr": {"value": 4.63},
                        "score_product": {"value": 4.78},
                        "score_deliver": {"value": 4.43},
                        "score_service": {"value": 4.62},
                    }
                },
                "refresh": {},
                "last_attempt_at": "",
            }
        ),
    )

    controller._record_store_score_refresh_result(
        store_id,
        {
            "dsr": 4.63,
            "score_product": 4.78,
            "score_deliver": 4.43,
            "score_service": 4.62,
            "_score_updated_at": "2026-11-24 10:05:00",
            "_score_source": "微信小店统计接口 scoreTypeList 11/12/13/14",
            "_score_attempt_status": {
                "dsr": "updated",
                "score_product": "updated",
                "score_deliver": "updated",
                "score_service": "updated",
            },
            "_score_daily_values": {
                "dsr": [
                    {"date": "2026-11-23", "value": 4.63},
                    {"date": "2026-11-22", "value": 4.63},
                ],
                "score_product": [
                    {"date": "2026-11-23", "value": 4.78},
                    {"date": "2026-11-22", "value": 4.73},
                ],
                "score_deliver": [
                    {"date": "2026-11-23", "value": 4.43},
                    {"date": "2026-11-22", "value": 4.43},
                ],
                "score_service": [
                    {"date": "2026-11-23", "value": 4.62},
                    {"date": "2026-11-22", "value": 4.64},
                ],
            },
        },
    )

    listed = controller.dispatch("stores.list", {})["stores"][0]
    assert "dsr" not in listed["score_changes"]
    assert listed["score_changes"]["score_product"] == {
        "from": 4.73,
        "to": 4.78,
        "delta": 0.05,
        "updated_at": "2026-11-24 10:05:00",
        "baseline_date": "2026-11-22",
        "comparison_date": "2026-11-23",
    }
    assert "score_deliver" not in listed["score_changes"]
    assert listed["score_changes"]["score_service"]["delta"] == -0.02
    assert listed["score_changes"]["score_service"]["baseline_date"] == "2026-11-22"
    assert listed["score_changes"]["score_service"]["comparison_date"] == "2026-11-23"

    state = json.loads(
        database.get_setting(
            f"webui_store_score_state_{store_id}",
            "{}",
        )
    )
    assert state["daily"]["2026-11-23"]["score_product"]["official"] is True
    assert state["daily"]["2026-11-22"]["score_product"] == {
        "value": 4.73,
        "updated_at": "2026-11-24 10:05:00",
        "source": "微信小店统计接口 scoreTypeList 11/12/13/14",
        "official": True,
    }


def test_unknown_score_does_not_create_a_trend_arrow(
    tmp_path: Path,
) -> None:
    controller, _database, store_id = _controller(tmp_path)
    controller._record_store_score_refresh_result(
        store_id,
        {
            "dsr": None,
            "_score_attempt_status": {"dsr": "missing"},
        },
    )

    listed = controller.dispatch("stores.list", {})["stores"][0]

    assert listed["metrics"].get("dsr") is None
    assert "dsr" not in listed["score_changes"]
    assert "dsr" not in listed["score_trends"]


def test_concurrent_identical_score_results_keep_one_stable_change(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 11, 21, 23, 50, 0)]
    controller, database, store_id = _controller(
        tmp_path,
        now_provider=lambda: current[0],
    )
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps({"dsr": 4.60}),
    )
    controller._record_store_score_baseline({store_id})
    current[0] = datetime(2026, 11, 22, 10, 5, 0)
    result = {
        "dsr": 4.70,
        "_score_updated_at": "2026-11-22 10:00:00",
        "_score_attempt_status": {"dsr": "updated"},
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                controller._record_store_score_refresh_result,
                store_id,
                result,
            )
            for _ in range(2)
        ]
        for future in futures:
            future.result()

    listed = controller.dispatch("stores.list", {})["stores"][0]
    assert listed["score_changes"]["dsr"]["from"] == 4.60
    assert listed["score_changes"]["dsr"]["to"] == 4.70
    assert listed["score_changes"]["dsr"]["delta"] == 0.10


def test_smoke_store_trends_remain_stable_across_live_lists() -> None:
    from weimi_manager.webui.main import _smoke_controller

    controller, _data_dir = _smoke_controller()
    first = controller.dispatch("stores.list", {})["stores"][0]
    second = controller.dispatch("stores.list", {})["stores"][0]

    assert first["score_changes"]["dsr"]["delta"] == 0.10
    assert second["score_changes"] == first["score_changes"]


def test_legacy_previous_baseline_is_migrated_without_losing_current_values(
    tmp_path: Path,
) -> None:
    controller, database, store_id = _controller(tmp_path)
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "dsr": 4.73,
                "score_product": 4.70,
                "score_deliver": 4.58,
                "score_service": 4.81,
                "stats_at": "2026-11-22 09:00:00",
            }
        ),
    )
    database.set_setting(
        f"webui_previous_store_metrics_{store_id}",
        json.dumps(
            {
                "dsr": 4.65,
                "score_product": 4.70,
                "score_deliver": 4.65,
                "score_service": 4.81,
            }
        ),
    )

    first = controller.dispatch("stores.list", {})["stores"][0]
    repeated = controller.dispatch("stores.list", {})["stores"][0]

    assert first["score_changes"] == {}
    assert repeated["score_changes"] == first["score_changes"]
    assert first["score_state"]["values"]["dsr"]["updated_at"] == "2026-11-22 09:00:00"
    assert first["score_state"]["daily"]["2026-11-22"]["dsr"]["value"] == 4.73


def test_deleting_store_purges_scoped_caches_without_touching_other_stores(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-cache-delete.db")
    deleted_id = database.add_store(
        "待删除店铺",
        "wx-cache-delete",
        "secret",
    )
    keep_id = database.add_store(
        "保留店铺",
        "wx-cache-keep",
        "secret",
    )

    deleted_keys = [
        f"store_metrics_{deleted_id}",
        f"webui_store_score_state_{deleted_id}",
        f"webui_previous_store_metrics_{deleted_id}",
        f"product_quota_{deleted_id}",
        f"after_sales_last_sync_{deleted_id}",
        f"analytics_product_backfill_at_{deleted_id}",
        f"double11_prefill_{deleted_id}",
        f"double11_last_result_{deleted_id}",
        f"hosted_listing_mode_{deleted_id}",
        f"hosted_listing_count_{deleted_id}",
        f"hosted_config_extra_{deleted_id}",
        f"activity_last_result_{deleted_id}_activity-1",
    ]
    for key in deleted_keys:
        database.set_setting(key, '{"value": 1}')
    database.set_setting(
        f"store_metrics_{keep_id}",
        json.dumps({"dsr": 4.75}),
    )
    database.set_setting("unrelated_setting", "keep")

    database.delete_store(deleted_id)

    assert all(
        database.get_setting(key, "") == ""
        for key in deleted_keys
    )
    assert database.get_setting(
        f"store_metrics_{keep_id}",
        "",
    )
    assert database.get_setting("unrelated_setting") == "keep"


def test_controller_startup_prunes_only_orphaned_store_caches(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "orphan-cache.db")
    live_id = database.add_store(
        "现存店铺",
        "wx-orphan-live",
        "secret",
    )
    orphan_id = live_id + 1000
    database.set_setting(
        f"store_metrics_{live_id}",
        json.dumps({"dsr": 4.71}),
    )
    database.set_setting(
        f"webui_store_score_state_{live_id}",
        json.dumps({"values": {"dsr": {"value": 4.71}}}),
    )
    database.set_setting(
        f"store_metrics_{orphan_id}",
        json.dumps({"dsr": 4.68}),
    )
    database.set_setting(
        f"webui_store_score_state_{orphan_id}",
        json.dumps({"values": {"dsr": {"value": 4.68}}}),
    )

    NextUiController(database)

    assert database.get_setting(f"store_metrics_{orphan_id}", "") == ""
    assert (
        database.get_setting(
            f"webui_store_score_state_{orphan_id}",
            "",
        )
        == ""
    )
    assert database.get_setting(f"store_metrics_{live_id}", "")
    assert database.get_setting(
        f"webui_store_score_state_{live_id}",
        "",
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"_cache_store_id": 999},
        {"_cache_store_app_id": "wx-another-store"},
    ],
)
def test_metrics_and_score_state_with_mismatched_owner_are_ignored(
    tmp_path: Path,
    metadata: dict,
) -> None:
    controller, database, store_id = _controller(tmp_path)
    contaminated_metrics = {
        "dsr": 4.68,
        "score_product": 4.70,
        "score_deliver": 4.60,
        "score_service": 4.69,
        **metadata,
    }
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(contaminated_metrics),
    )
    database.set_setting(
        f"webui_store_score_state_{store_id}",
        json.dumps(
            {
                "_owner_store_id": metadata.get("_cache_store_id", store_id),
                "_owner_store_app_id": metadata.get(
                    "_cache_store_app_id",
                    "wx-score-state",
                ),
                "values": {
                    key: {"value": value}
                    for key, value in {
                        "dsr": 4.68,
                        "score_product": 4.70,
                        "score_deliver": 4.60,
                        "score_service": 4.69,
                    }.items()
                },
                "changes": {},
                "refresh": {},
            }
        ),
    )

    listed = controller.dispatch("stores.list", {})["stores"][0]

    for key in STORE_SCORE_FIELDS:
        assert key not in listed["metrics"]
        assert key not in listed["score_state"]["values"]
    assert listed["score_changes"] == {}


def test_concurrent_refresh_results_remain_bound_to_the_originating_store(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "ordered-store-refresh.db")
    first_id = database.add_store("异步店铺一", "wx-async-1", "secret")
    second_id = database.add_store("异步店铺二", "wx-async-2", "secret")
    empty_id = database.add_store("零商品店铺", "wx-async-empty", "secret")
    stores = {
        store.id: store
        for store in database.list_stores()
    }

    class DelayedService:
        def refresh_store_metrics(self, store):
            if store.id == second_id:
                time.sleep(0.04)
            scores = {
                first_id: {
                    "dsr": 4.51,
                    "score_product": 4.52,
                    "score_deliver": 4.53,
                    "score_service": 4.54,
                },
                second_id: {
                    "dsr": 4.61,
                    "score_product": 4.62,
                    "score_deliver": 4.63,
                    "score_service": 4.64,
                },
                empty_id: {
                    "dsr": None,
                    "score_product": None,
                    "score_deliver": None,
                    "score_service": None,
                },
            }[store.id]
            metrics = {
                **scores,
                "_cache_store_id": store.id,
                "_cache_store_app_id": store.app_id,
                "_score_attempt_status": {
                    key: (
                        "updated"
                        if value is not None
                        else "missing"
                    )
                    for key, value in scores.items()
                },
            }
            database.set_setting(
                f"store_metrics_{store.id}",
                json.dumps(metrics),
            )
            return metrics

    controller = NextUiController(
        database,
        product_service=DelayedService(),
    )
    result = controller._refresh_stores({})
    assert result["succeeded"] == 3

    listed = {
        row["id"]: row
        for row in controller.dispatch("stores.list", {})["stores"]
    }
    assert listed[first_id]["metrics"]["dsr"] == 4.51
    assert listed[second_id]["metrics"]["dsr"] == 4.61
    assert listed[empty_id]["metrics"].get("dsr") is None
    assert listed[empty_id]["score_state"]["values"] == {}
    assert set(stores) == {first_id, second_id, empty_id}


class _MetricsPrivateClient:
    def __init__(
        self,
        *,
        deposit_value: int | None = 50000,
        deposit_error: str = "",
        status_counts_error: str = "",
    ) -> None:
        self.deposit_value = deposit_value
        self.deposit_error = deposit_error
        self.status_counts_error = status_counts_error

    def realtime_overview(self):
        return {}

    def todo_overview(self):
        return {}

    def score_overview(self):
        return {
            "dsr": 4.80,
            "score_product": None,
            "score_deliver": 4.55,
            "score_service": None,
        }

    def evaluation_overview(self):
        return {}

    def account_balance(self):
        return {}

    def deposit_balance(self):
        if self.deposit_error:
            raise RuntimeError(self.deposit_error)
        return self.deposit_value

    def recommend_overview(self, yesterday=False, *, days=None):
        if days == 7:
            return {
                "exposure": 90,
                "exposure_count": 900,
                "exposure_people": 90,
            }
        if yesterday:
            return {
                "exposure": 80,
                "exposure_count": 800,
                "exposure_people": 80,
            }
        return {
            "exposure": 100,
            "exposure_count": 1000,
            "exposure_people": 100,
        }

    def status_counts(self):
        if self.status_counts_error:
            raise RuntimeError(self.status_counts_error)
        return {"sales": 17, "offline": 3}

    def search_overview(self, _days):
        return {"search_exposure_yesterday": 0}

    def audit_success_today(self):
        return 0


def test_refresh_metrics_preserves_last_successful_scores_and_deposit(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "metrics-preserve.db")
    store_id = database.add_store("指标保护店铺", "wx-metrics-preserve", "secret")
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "dsr": 4.70,
                "score_product": 4.60,
                "score_deliver": 4.50,
                "score_service": 4.40,
                "deposit": 32100,
                "_deposit_source": "微信小店 getDepositMPayState",
                "_deposit_updated_at": "2026-11-22 08:00:00",
            }
        ),
    )
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)
    private = _MetricsPrivateClient(deposit_value=None)
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch("weimi_manager.service.ShopPrivateClient", return_value=private),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["dsr"] == 4.80
    assert metrics["score_product"] == 4.60
    assert metrics["score_deliver"] == 4.55
    assert metrics["score_service"] == 4.40
    assert metrics["deposit"] == 32100
    assert metrics["recommend_exposure"] == 100
    assert metrics["recommend_exposure_yesterday"] == 80
    assert metrics["recommend_exposure_7d"] == 90
    assert metrics["recommend_exposure_count"] == 1000
    assert metrics["recommend_exposure_yesterday_count"] == 800
    assert metrics["recommend_exposure_7d_count"] == 900
    assert metrics["recommend_exposure_people"] == 100
    assert metrics["recommend_exposure_yesterday_people"] == 80
    assert metrics["recommend_exposure_7d_people"] == 90
    assert metrics["in_sale"] == 17
    assert metrics["_exposure_attempt_status"] == {
        "today": "updated",
        "yesterday": "updated",
        "seven_days": "updated",
    }
    assert metrics["_exposure_count_attempt_status"] == {
        "today": "updated",
        "yesterday": "updated",
        "seven_days": "updated",
    }
    assert metrics["_exposure_people_attempt_status"] == {
        "today": "updated",
        "yesterday": "updated",
        "seven_days": "updated",
    }
    assert metrics["_online_product_attempt_status"] == "updated"
    assert "status=5" in metrics["_online_product_source"] or "销售中" in metrics["_online_product_source"]
    assert metrics["_score_attempt_status"] == {
        "dsr": "updated",
        "score_product": "missing",
        "score_deliver": "updated",
        "score_service": "missing",
    }
    assert metrics["_deposit_attempt_status"] == "missing"


def test_refresh_metrics_propagates_official_daily_score_history(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "metrics-daily-history.db")
    store_id = database.add_store(
        "评分历史店铺",
        "wx-metrics-daily-history",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)
    private = _MetricsPrivateClient()
    daily = {
        "score_product": [
            {"date": "2026-11-23", "value": 4.78},
            {"date": "2026-11-22", "value": 4.73},
        ]
    }
    private.score_overview = lambda: {
        "dsr": 4.63,
        "score_product": 4.78,
        "score_deliver": 4.43,
        "score_service": 4.62,
        "_score_daily_values": daily,
    }
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch("weimi_manager.service.ShopPrivateClient", return_value=private),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["_score_daily_values"] == daily


def test_refresh_metrics_preserves_deposit_when_api_fails(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "deposit-failure.db")
    store_id = database.add_store("保证金失败店铺", "wx-deposit-fail", "secret")
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "deposit": 45600,
                "_deposit_source": "微信小店 getDepositMPayState",
                "_deposit_updated_at": "2026-11-21 09:00:00",
            }
        ),
    )
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)
    private = _MetricsPrivateClient(deposit_error="保证金接口超时")
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch("weimi_manager.service.ShopPrivateClient", return_value=private),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["deposit"] == 45600
    assert metrics["_deposit_attempt_status"] == "failed"
    assert "保证金接口超时" in metrics["_deposit_error"]


def test_refresh_metrics_prefers_official_in_sale_count(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-online-products.db")
    store_id = database.add_store("官方在售数店铺", "wx-official-online", "secret")
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)
    private = _MetricsPrivateClient()

    class OfficialClient:
        def fetch_product_total(self, status=None):
            return {None: 40, 5: 31, 11: 9}[status]

    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", return_value=OfficialClient()),
        patch("weimi_manager.service.ShopPrivateClient", return_value=private),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["in_sale"] == 31
    assert metrics["_online_product_attempt_status"] == "updated"
    assert "微信开放平台" in metrics["_online_product_source"]


def test_refresh_metrics_leaves_online_count_missing_when_sources_fail(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "missing-online-products.db")
    store_id = database.add_store("在线数缺失店铺", "wx-missing-online", "secret")
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)
    private = _MetricsPrivateClient(status_counts_error="商家后台商品状态失败")

    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch("weimi_manager.service.ShopPrivateClient", return_value=private),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["in_sale"] is None
    assert metrics["_online_product_attempt_status"] == "failed"
    assert "商家后台商品状态失败" in metrics["_online_product_error"]


def test_refresh_metrics_keeps_exposure_count_and_people_independent(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "partial-exposure.db")
    store_id = database.add_store("曝光半边店铺", "wx-partial-exposure", "secret")
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)

    class PartialExposureClient(_MetricsPrivateClient):
        def recommend_overview(self, yesterday=False, *, days=None):
            if days == 7:
                return {
                    "exposure": None,
                    "exposure_count": None,
                    "exposure_people": None,
                }
            if yesterday:
                return {
                    "exposure": 17,
                    "exposure_count": None,
                    "exposure_people": 17,
                }
            return {
                "exposure": 21,
                "exposure_count": 21,
                "exposure_people": None,
            }

    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=PartialExposureClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["recommend_exposure_count"] == 21
    assert metrics["recommend_exposure_people"] is None
    assert metrics["recommend_exposure_yesterday_count"] is None
    assert metrics["recommend_exposure_yesterday_people"] == 17
    assert metrics["recommend_exposure_7d_count"] is None
    assert metrics["recommend_exposure_7d_people"] is None
    assert metrics["_exposure_attempt_status"] == {
        "today": "partial",
        "yesterday": "partial",
        "seven_days": "missing",
    }
    assert metrics["_exposure_count_attempt_status"] == {
        "today": "updated",
        "yesterday": "missing",
        "seven_days": "missing",
    }
    assert metrics["_exposure_people_attempt_status"] == {
        "today": "missing",
        "yesterday": "updated",
        "seven_days": "missing",
    }


def test_refresh_store_metrics_prefers_official_deal_statistics(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-sales.db")
    store_id = database.add_store("成交统计店铺", "wx-official-sales", "secret")
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)
    period_calls = []

    class OfficialSalesClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 13458,
                "pay_order_cnt": 8,
                "pay_uv": 4,
            }

        def period_overview(self, days=1, offset_days=1):
            period_calls.append((days, offset_days))
            values = {
                (1, 1): (10000, 6, 3),
                (1, 0): (13458, 8, 4),
                (7, 1): (50000, 20, 12),
                (30, 1): (200000, 70, 41),
            }
            gmv, orders, people = values[(days, offset_days)]
            return {
                "pay_gmv": gmv,
                "pay_order_cnt": orders,
                "pay_uv": people,
            }

    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=OfficialSalesClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["gmv_today"] == 13458
    assert metrics["orders_today"] == 8
    assert metrics["buyers_today"] == 4
    assert metrics["gmv_yesterday"] == 10000
    assert metrics["orders_yesterday"] == 6
    assert metrics["buyers_yesterday"] == 3
    assert metrics["gmv_7d"] == 50000
    assert metrics["orders_7d"] == 20
    assert metrics["buyers_7d"] == 12
    assert metrics["gmv_30d"] == 200000
    assert metrics["orders_30d"] == 70
    assert metrics["buyers_30d"] == 41
    assert metrics["_sales_attempt_status"] == {
        "today": "updated",
        "yesterday": "updated",
        "seven_days": "updated",
        "thirty_days": "updated",
    }
    assert "微信小店实时成交统计" in metrics["_sales_source"]
    assert "近7天成交统计" in metrics["_sales_source"]
    assert "近30天成交统计" in metrics["_sales_source"]
    assert metrics["_sales_updated_at"]
    assert metrics["_sales_is_stale"] is False
    assert period_calls == [(1, 1), (7, 1), (30, 1)]


def test_official_sales_excludes_current_unclaimed_gifts_by_period(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-sales-gifts.db")
    store_id = database.add_store(
        "礼物成交统计店铺",
        "wx-official-sales-gifts",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    fixed_now = datetime(2026, 11, 23, 12, 0, 0)

    def add_order(
        order_id: str,
        status: int,
        created_at: datetime,
        amount: int,
        raw: dict,
    ) -> None:
        database.upsert_order(
            Order(
                store_id=store.id,
                store_name=store.name,
                order_id=order_id,
                status=status,
                status_text="",
                create_time=int(created_at.timestamp()),
                update_time=int(created_at.timestamp()),
                pay_time=int(created_at.timestamp()),
                total_amount=amount,
                product_amount=amount,
                freight_amount=0,
                discount_amount=0,
                buyer_name=order_id,
                phone="",
                address="",
                waybill_id="",
                carrier="",
                merchant_notes="",
                raw=raw,
            )
        )

    add_order(
        "GIFT-TODAY",
        12,
        fixed_now - timedelta(hours=2),
        50000,
        {},
    )
    add_order(
        "GIFT-YESTERDAY",
        12,
        datetime(2026, 11, 22, 18, 0, 0),
        10000,
        {"is_present": True},
    )
    add_order(
        "GIFT-EXACT-24H",
        12,
        fixed_now - timedelta(hours=24),
        11000,
        {"is_present": True},
    )
    add_order(
        "GIFT-EXPIRED-3D",
        12,
        fixed_now - timedelta(days=3),
        70000,
        {"is_present": True},
    )
    add_order(
        "GIFT-EXACT-WEEK",
        12,
        datetime.combine(
            fixed_now.date() - timedelta(days=7),
            datetime.min.time(),
        ),
        80000,
        {"is_present": True},
    )
    add_order(
        "GIFT-EXACT-MONTH",
        12,
        datetime.combine(
            fixed_now.date() - timedelta(days=30),
            datetime.min.time(),
        ),
        90000,
        {"is_present": True},
    )
    for order_id, status, amount in (
        ("CLAIMED", 20, 40000),
        ("REFUNDED", 200, 30000),
        ("CANCELLED", 250, 20000),
    ):
        add_order(
            order_id,
            status,
            fixed_now - timedelta(hours=1),
            amount,
            {"is_present": True},
        )

    class OfficialGiftSalesClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 999999,
                "pay_order_cnt": 66,
                "pay_uv": 5,
            }

        def period_overview(self, days=1, offset_days=1):
            values = {
                (1, 1): (200000, 10, 4),
                (7, 1): (700000, 30, 12),
                (30, 1): (900000, 40, 20),
            }
            gmv, orders, people = values[(days, offset_days)]
            return {
                "pay_gmv": gmv,
                "pay_order_cnt": orders,
                "pay_uv": people,
            }

    service = SyncService(database, now_provider=lambda: fixed_now)
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=OfficialGiftSalesClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["gmv_today"] == 949999
    assert metrics["orders_today"] == 65
    assert metrics["today_orders"] == 65
    assert metrics["buyers_today"] == 5
    assert metrics["gmv_yesterday"] == 190000
    assert metrics["orders_yesterday"] == 9
    assert metrics["gmv_7d"] == 690000
    assert metrics["orders_7d"] == 29
    assert metrics["gmv_30d"] == 890000
    assert metrics["orders_30d"] == 39
    exclusion = metrics["_gift_exclusion"]
    assert exclusion["today"]["deducted_amount"] == 50000
    assert exclusion["today"]["applied_amount"] == 50000
    assert exclusion["today"]["deducted_orders"] == 1
    assert exclusion["today"]["pending_order_ids"] == ["GIFT-TODAY"]
    assert exclusion["today"]["unconfirmed_count"] == 1
    assert exclusion["yesterday"]["deducted_amount"] == 10000
    assert exclusion["yesterday"]["pending_order_ids"] == ["GIFT-YESTERDAY"]
    assert "GIFT-EXACT-24H" in exclusion["yesterday"][
        "expired_status12_order_ids"
    ]
    assert exclusion["seven_days"]["deducted_amount"] == 10000
    assert exclusion["thirty_days"]["deducted_amount"] == 10000
    assert "未领取礼物" in metrics["_sales_period_sources"]["today"]
    assert "未领取礼物" in metrics["_sales_period_sources"]["yesterday"]
    assert "未领取礼物" in metrics["_sales_period_sources"]["seven_days"]
    assert "未领取礼物" in metrics["_sales_period_sources"]["thirty_days"]


def test_official_sales_without_gifts_is_unchanged(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-sales-no-gifts.db")
    store_id = database.add_store(
        "无礼物成交店铺",
        "wx-official-sales-no-gifts",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None

    class OfficialSalesClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 123456,
                "pay_order_cnt": 7,
                "pay_uv": 3,
            }

        def period_overview(self, days=1, offset_days=1):
            return {
                "pay_gmv": 500000,
                "pay_order_cnt": 20,
                "pay_uv": 10,
            }

    service = SyncService(
        database,
        now_provider=lambda: datetime(2026, 11, 23, 12, 0, 0),
    )
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=OfficialSalesClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["gmv_today"] == 123456
    assert metrics["orders_today"] == 7
    assert metrics["gmv_yesterday"] == 500000
    assert metrics["orders_yesterday"] == 20
    assert metrics["gmv_7d"] == 500000
    assert metrics["orders_7d"] == 20
    assert metrics["gmv_30d"] == 500000
    assert metrics["orders_30d"] == 20
    assert metrics["refund_orders_today"] is None
    assert metrics["_refund_order_cache"]["status"] == "missing"
    assert all(
        period["deducted_amount"] == 0
        and period["deducted_orders"] == 0
        for period in metrics["_gift_exclusion"].values()
    )


def test_refund_periods_use_official_amounts_and_deduplicated_aftersale_orders(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "refund-periods.db")
    store_id = database.add_store(
        "退款周期店铺",
        "wx-refund-periods",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    fixed_now = datetime(2026, 11, 23, 12, 0, 0)
    def add_after_sale(
        after_sale_id: str,
        order_id: str,
        status: str,
        created_at: datetime,
    ) -> None:
        database.upsert_after_sale(
            store.id,
            {
                "after_sale_order_id": after_sale_id,
                "order_id": order_id,
                "status": status,
                "type": "refund",
                "reason_text": "测试",
                "refund_info": {"amount": 100},
                "create_time": int(created_at.timestamp()),
                "update_time": int(created_at.timestamp()),
            },
        )

    add_after_sale(
        "AS-TODAY-1",
        "ORDER-TODAY",
        "MERCHANT_PROCESSING",
        fixed_now - timedelta(hours=2),
    )
    add_after_sale(
        "AS-TODAY-DUPLICATE",
        "ORDER-TODAY",
        "PLATFORM_REFUNDING",
        fixed_now - timedelta(hours=1),
    )
    add_after_sale(
        "AS-TODAY-CLOSED",
        "ORDER-CLOSED",
        "MERCHANT_FAIL",
        fixed_now - timedelta(hours=3),
    )
    add_after_sale(
        "AS-TODAY-SUCCESS-1",
        "ORDER-TODAY",
        "MERCHANT_REFUND_SUCCESS",
        fixed_now - timedelta(hours=1, minutes=30),
    )
    add_after_sale(
        "AS-TODAY-SUCCESS-2",
        "ORDER-TODAY",
        "MERCHANT_RETURN_SUCCESS",
        fixed_now - timedelta(hours=1),
    )
    add_after_sale(
        "AS-CANCELLED",
        "ORDER-CANCELLED",
        "USER_CANCELD",
        fixed_now - timedelta(hours=1),
    )
    add_after_sale(
        "AS-YESTERDAY",
        "ORDER-YESTERDAY",
        "MERCHANT_REFUND_SUCCESS",
        datetime(2026, 11, 22, 10, 0, 0),
    )
    add_after_sale(
        "AS-WEEK",
        "ORDER-WEEK",
        "RETURN_CLOSED",
        datetime(2026, 11, 16, 0, 0, 0),
    )
    add_after_sale(
        "AS-WEEK-SUCCESS",
        "ORDER-WEEK-SUCCESS",
        "MERCHANT_REFUND_SUCCESS",
        datetime(2026, 11, 16, 10, 0, 0),
    )
    add_after_sale(
        "AS-MONTH",
        "ORDER-MONTH",
        "MERCHANT_REJECT_REFUND",
        datetime(2026, 10, 24, 0, 0, 0),
    )
    add_after_sale(
        "AS-MONTH-SUCCESS",
        "ORDER-MONTH-SUCCESS",
        "MERCHANT_RETURN_SUCCESS",
        datetime(2026, 10, 24, 10, 0, 0),
    )
    database.set_setting(
        f"after_sales_last_sync_{store.id}",
        str(int(fixed_now.timestamp())),
    )
    database.set_setting(
        f"after_sales_coverage_start_{store.id}",
        str(int(datetime(2026, 10, 24, 0, 0, 0).timestamp())),
    )
    database.set_setting(
        f"after_sales_coverage_end_{store.id}",
        str(int(fixed_now.timestamp())),
    )

    class RefundSalesClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 100000,
                "pay_order_cnt": 10,
                "pay_uv": 5,
                "pay_refund_gmv": 1200,
            }

        def period_overview(self, days=1, offset_days=1):
            refunds = {(1, 1): 2200, (7, 1): 3200, (30, 1): 4200}
            return {
                "pay_gmv": 500000,
                "pay_order_cnt": 20,
                "pay_uv": 10,
                "pay_refund_gmv": refunds[(days, offset_days)],
            }

    service = SyncService(database, now_provider=lambda: fixed_now)
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=RefundSalesClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["refund_gmv_today"] == 1200
    assert metrics["refund_gmv_yesterday"] == 2200
    assert metrics["refund_gmv_7d"] == 3200
    assert metrics["refund_gmv_30d"] == 4200
    assert metrics["refund_orders_today"] == 1
    assert metrics["refund_orders_yesterday"] == 1
    assert metrics["refund_orders_7d"] == 2
    assert metrics["refund_orders_30d"] == 3
    assert metrics["_refund_order_cache"]["status"] == "fresh"
    assert metrics["_refund_order_cache"]["hint"] == ""
    assert metrics["_refund_attempt_status"] == {
        "today": "updated",
        "yesterday": "updated",
        "seven_days": "updated",
        "thirty_days": "updated",
    }
    add_after_sale(
        "AS-TODAY-NEW-COMPLETION",
        "ORDER-TODAY-NEW",
        "MERCHANT_REFUND_SUCCESS",
        fixed_now - timedelta(minutes=20),
    )
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=RefundSalesClient(),
        ),
    ):
        refreshed_metrics = service.refresh_store_metrics(store)
    assert refreshed_metrics["refund_orders_today"] == 2
    database.set_setting(
        f"after_sales_last_sync_{store.id}",
        str(int((fixed_now - timedelta(days=2)).timestamp())),
    )
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=RefundSalesClient(),
        ),
    ):
        stale_metrics = service.refresh_store_metrics(store)
    assert stale_metrics["_refund_order_cache"]["status"] == "stale"
    assert "售后单量缓存待刷新" in stale_metrics["_refund_order_cache"]["hint"]


def test_refund_counts_ignore_order_status_and_deduplicate_official_aftersales() -> None:
    fixed_now = datetime(2026, 11, 23, 12, 0, 0)
    today_start = int(
        datetime(2026, 11, 23, 0, 0, 0).timestamp()
    )
    yesterday_start = int(
        datetime(2026, 11, 22, 0, 0, 0).timestamp()
    )
    week_start = int(
        datetime(2026, 11, 16, 0, 0, 0).timestamp()
    )
    month_start = int(
        datetime(2026, 10, 24, 0, 0, 0).timestamp()
    )
    ranges = {
        "today": (today_start, int(fixed_now.timestamp()) + 1),
        "yesterday": (yesterday_start, today_start),
        "seven_days": (week_start, today_start),
        "thirty_days": (month_start, today_start),
    }
    orders = [
        {
            "order_id": "ORDER-TODAY",
            "status": 200,
            "status_text": "售后退款完成",
            "update_time": int(
                datetime(2026, 11, 23, 10, 0, 0).timestamp()
            ),
            "raw_json": json.dumps(
                {
                    "order_detail": {
                        "product_infos": [
                            {"finish_aftersale_sku_cnt": "1"}
                        ]
                    }
                }
            ),
        },
        {
            "order_id": "ORDER-YESTERDAY",
            "status": 200,
            "status_text": "售后退款完成",
            "update_time": int(
                datetime(2026, 11, 22, 10, 0, 0).timestamp()
            ),
            "raw_json": "{}",
        },
        {
            "order_id": "ORDER-WEEK",
            "status": 200,
            "status_text": "售后退款完成",
            "update_time": int(
                datetime(2026, 11, 17, 10, 0, 0).timestamp()
            ),
            "raw_json": "{}",
        },
        {
            "order_id": "ORDER-MONTH",
            "status": 200,
            "status_text": "售后退款完成",
            "update_time": int(
                datetime(2026, 10, 25, 10, 0, 0).timestamp()
            ),
            "raw_json": "{}",
        },
        {
            "order_id": "ORDER-CANCELLED",
            "status": 250,
            "status_text": "已取消",
            "update_time": int(fixed_now.timestamp()),
            "raw_json": "{}",
        },
    ]
    after_sales = [
        {
            "after_sale_order_id": "AS-DUPLICATE",
            "order_id": "ORDER-TODAY",
            "status": "MERCHANT_PROCESSING",
            "status_text": "商家处理中",
            "create_time": int(fixed_now.timestamp()) - 1800,
            "update_time": int(fixed_now.timestamp()) - 1800,
        },
        {
            "after_sale_order_id": "AS-TODAY-1",
            "order_id": "ORDER-TODAY",
            "status": "MERCHANT_REFUND_SUCCESS",
            "status_text": "退款成功",
            "create_time": int(fixed_now.timestamp()) - 1800,
            "update_time": int(fixed_now.timestamp()) - 1800,
        },
        {
            "after_sale_order_id": "AS-TODAY-2",
            "order_id": "ORDER-TODAY",
            "status": "MERCHANT_RETURN_SUCCESS",
            "status_text": "退货退款完成",
            "create_time": int(fixed_now.timestamp()) - 1200,
            "update_time": int(fixed_now.timestamp()) - 1200,
        },
        {
            "after_sale_order_id": "AS-YESTERDAY",
            "order_id": "ORDER-AFTERSALE-YESTERDAY",
            "status": "MERCHANT_REFUND_SUCCESS",
            "status_text": "退款成功",
            "create_time": int(
                datetime(2026, 11, 21, 20, 0, 0).timestamp()
            ),
            "update_time": int(
                datetime(2026, 11, 22, 9, 0, 0).timestamp()
            ),
        },
        {
            "after_sale_order_id": "AS-WEEK",
            "order_id": "ORDER-AFTERSALE-WEEK",
            "status": "MERCHANT_REFUND_SUCCESS",
            "status_text": "退款成功",
            "create_time": int(
                datetime(2026, 11, 16, 9, 0, 0).timestamp()
            ),
            "update_time": int(
                datetime(2026, 11, 16, 13, 0, 0).timestamp()
            ),
        },
        {
            "after_sale_order_id": "AS-MONTH",
            "order_id": "ORDER-AFTERSALE-MONTH",
            "status": "MERCHANT_RETURN_SUCCESS",
            "status_text": "退货退款完成",
            "create_time": int(
                datetime(2026, 10, 24, 9, 0, 0).timestamp()
            ),
            "update_time": int(
                datetime(2026, 10, 24, 13, 0, 0).timestamp()
            ),
        },
        {
            "after_sale_order_id": "AS-CANCELLED",
            "order_id": "ORDER-AFTERSALE-CANCELLED",
            "status": "USER_CANCELD",
            "status_text": "用户已取消",
            "create_time": int(fixed_now.timestamp()) - 1800,
            "update_time": int(fixed_now.timestamp()) - 1800,
        },
    ]

    counts, metadata = _refund_order_counts_for_periods(
        orders,
        after_sales,
        ranges,
    )

    assert counts == {
        "today": 1,
        "yesterday": 1,
        "seven_days": 2,
        "thirty_days": 3,
    }
    assert metadata["record_count"] == 4
    assert any(
        "官方售后完成时间" in source
        for source in metadata["sources"]["today"]
    )
    assert metadata["order_status_evidence"] == {
        "today": 1,
        "yesterday": 1,
        "seven_days": 2,
        "thirty_days": 3,
    }
    assert "不参与单量" in metadata["source"]


def test_store_metrics_refreshes_recent_after_sale_updates(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "after-sale-refresh.db")
    store_id = database.add_store(
        "退款刷新店铺",
        "wx-after-sale-refresh",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    now_ts = int(datetime(2026, 11, 24, 14, 0, 0).timestamp())
    service = SyncService(database)
    calls: list[tuple[int, int, str]] = []

    def sync_after_sales(
        _store,
        start_time,
        end_time,
        *,
        time_field="create",
    ):
        calls.append((start_time, end_time, time_field))
        return SyncResult("after_sales", 1, "ok", success=1)

    service.sync_after_sales = sync_after_sales
    result = service._sync_recent_after_sales_for_metrics(
        store,
        now_ts,
    )

    expected_full_start = int(
        datetime(
            2026,
            10,
            25,
            0,
            0,
            0,
            tzinfo=SHANGHAI_TZ,
        ).timestamp()
    )
    assert calls == [(expected_full_start, now_ts, "update")]
    assert result["status"] == "updated", result
    assert result["full_complete"] is True
    assert result["incremental_complete"] is True
    assert database.get_setting(f"after_sales_last_sync_{store_id}") == str(now_ts)
    assert database.get_setting(
        f"after_sales_coverage_start_{store_id}"
    ) == str(expected_full_start)
    assert database.get_setting(
        f"after_sales_coverage_end_{store_id}"
    ) == str(now_ts)

    calls.clear()
    second = service._sync_recent_after_sales_for_metrics(
        store,
        now_ts + 600,
    )
    assert calls == [(now_ts + 600 - 2 * 86400, now_ts + 600, "update")]
    assert second["status"] == "updated", second
    assert second["full_checked"] is False


def test_refund_count_is_unknown_when_official_amount_has_no_details(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "refund-gate.db")
    store_id = database.add_store(
        "退款明细缺失店铺",
        "wx-refund-gate",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    fixed_now = datetime(2026, 11, 23, 12, 0, 0)
    database.set_setting(
        f"after_sales_coverage_start_{store.id}",
        str(int(datetime(2026, 10, 24, 0, 0, 0).timestamp())),
    )
    database.set_setting(
        f"after_sales_coverage_end_{store.id}",
        str(int(fixed_now.timestamp())),
    )

    class MissingDetailClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 10000,
                "pay_order_cnt": 1,
                "pay_uv": 1,
                "pay_refund_gmv": 500,
            }

        def period_overview(self, days=1, offset_days=1):
            return {
                "pay_gmv": 10000,
                "pay_order_cnt": 1,
                "pay_uv": 1,
                "pay_refund_gmv": 500,
            }

    service = SyncService(
        database,
        now_provider=lambda: fixed_now,
    )
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=MissingDetailClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["refund_gmv_today"] == 500
    assert metrics["refund_orders_today"] is None
    assert metrics["refund_orders_yesterday"] is None
    assert metrics["_refund_order_cache"]["conflict_periods"] == [
        "today",
        "yesterday",
        "seven_days",
        "thirty_days",
    ]


def test_order_status_200_is_not_used_as_refund_count(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "refund-order-evidence.db")
    store_id = database.add_store(
        "退款覆盖证据店铺",
        "wx-refund-order-evidence",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    fixed_now = datetime(2026, 11, 23, 12, 0, 0)
    yesterday = datetime(2026, 11, 22, 10, 0, 0)
    for index in range(75):
        database.upsert_order(
            Order(
                store_id=store.id,
                store_name=store.name,
                order_id=f"ORDER-200-{index}",
                status=200,
                status_text="售后退款完成",
                create_time=int(
                    (fixed_now - timedelta(days=2)).timestamp()
                ),
                update_time=int(yesterday.timestamp()),
                pay_time=int(
                    (fixed_now - timedelta(days=2)).timestamp()
                ),
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
            ),
        )
    database.set_setting(
        f"after_sales_coverage_start_{store.id}",
        str(int(datetime(2026, 10, 24, 0, 0, 0).timestamp())),
    )
    database.set_setting(
        f"after_sales_coverage_end_{store.id}",
        str(int(fixed_now.timestamp())),
    )

    class ZeroRefundClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 0,
                "pay_order_cnt": 0,
                "pay_uv": 0,
                "pay_refund_gmv": 0,
            }

        def period_overview(self, days=1, offset_days=1):
            return {
                "pay_gmv": 0,
                "pay_order_cnt": 0,
                "pay_uv": 0,
                "pay_refund_gmv": 0,
            }

    service = SyncService(database, now_provider=lambda: fixed_now)
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=ZeroRefundClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["refund_gmv_yesterday"] == 0
    assert metrics["refund_orders_yesterday"] == 0
    assert metrics["_refund_order_cache"]["order_status_evidence"][
        "yesterday"
    ] == 75
    assert metrics["_refund_order_cache"]["conflict_periods"] == []


def test_completed_aftersale_with_zero_official_refund_is_a_conflict(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "refund-zero-conflict.db")
    store_id = database.add_store(
        "退款零额冲突店铺",
        "wx-refund-zero-conflict",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    fixed_now = datetime(2026, 11, 23, 12, 0, 0)
    database.upsert_after_sale(
        store.id,
        {
            "after_sale_order_id": "AS-ZERO-CONFLICT",
            "order_id": "ORDER-ZERO-CONFLICT",
            "status": "MERCHANT_REFUND_SUCCESS",
            "type": "refund",
            "refund_info": {"amount": 100},
            "create_time": int(fixed_now.timestamp()) - 3600,
            "update_time": int(fixed_now.timestamp()) - 1800,
        },
    )
    database.set_setting(
        f"after_sales_coverage_start_{store.id}",
        str(int(datetime(2026, 10, 24, 0, 0, 0).timestamp())),
    )
    database.set_setting(
        f"after_sales_coverage_end_{store.id}",
        str(int(fixed_now.timestamp())),
    )

    class ZeroRefundClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 1000,
                "pay_order_cnt": 1,
                "pay_uv": 1,
                "pay_refund_gmv": 0,
            }

        def period_overview(self, days=1, offset_days=1):
            return {
                "pay_gmv": 1000,
                "pay_order_cnt": 1,
                "pay_uv": 1,
                "pay_refund_gmv": 0,
            }

    service = SyncService(database, now_provider=lambda: fixed_now)
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=ZeroRefundClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["refund_gmv_today"] == 0
    assert metrics["refund_orders_today"] is None
    assert metrics["_refund_order_cache"]["conflict_periods"] == ["today"]
    assert "口径冲突" in metrics["_refund_order_cache"]["hint"]


def test_official_refund_count_fields_take_precedence(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-refund-counts.db")
    store_id = database.add_store(
        "官方退款单量店铺",
        "wx-official-refund-counts",
        "secret",
    )
    store = database.get_store(store_id)
    assert store is not None
    fixed_now = datetime(2026, 11, 23, 12, 0, 0)

    class OfficialRefundCountClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 10000,
                "pay_order_cnt": 1,
                "pay_uv": 1,
                "pay_refund_gmv": 100,
                "pay_refund_order_cnt": 1,
            }

        def period_overview(self, days=1, offset_days=1):
            counts = {(1, 1): 2, (7, 1): 3, (30, 1): 4}
            return {
                "pay_gmv": 10000,
                "pay_order_cnt": 1,
                "pay_uv": 1,
                "pay_refund_gmv": 100,
                "pay_refund_order_cnt": counts[(days, offset_days)],
            }

    service = SyncService(database, now_provider=lambda: fixed_now)
    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=OfficialRefundCountClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["refund_orders_today"] == 1
    assert metrics["refund_orders_yesterday"] == 2
    assert metrics["refund_orders_7d"] == 3
    assert metrics["refund_orders_30d"] == 4
    assert metrics["_refund_order_cache"]["official_count_fields"] == {
        "today": "微信小店实时成交统计（官方字段 pay_refund_order_cnt）",
        "yesterday": "微信小店后台昨日成交统计（官方字段 pay_refund_order_cnt）",
        "seven_days": "微信小店后台近7天成交统计（官方字段 pay_refund_order_cnt）",
        "thirty_days": "微信小店后台近30天成交统计（官方字段 pay_refund_order_cnt）",
    }


def test_official_sales_refreshes_new_value_and_preserves_last_success_on_failure(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-sales-refresh.db")
    store_id = database.add_store("成交更新店铺", "wx-sales-refresh", "secret")
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)

    class DynamicSalesClient(_MetricsPrivateClient):
        pay_gmv = 13458

        def realtime_overview(self):
            return {
                "pay_gmv": self.pay_gmv,
                "pay_order_cnt": 8,
                "pay_uv": 4,
            }

        def period_overview(self, days=1, offset_days=1):
            return {
                "pay_gmv": self.pay_gmv,
                "pay_order_cnt": 8,
                "pay_uv": 4,
            }

    client = DynamicSalesClient()
    patches = (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch("weimi_manager.service.ShopPrivateClient", return_value=client),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        first = service.refresh_store_metrics(store)
    client.pay_gmv = 16092
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        second = service.refresh_store_metrics(store)

    assert first["gmv_today"] == 13458
    assert second["gmv_today"] == 16092
    assert second["_sales_is_stale"] is False

    class FailingSalesClient(_MetricsPrivateClient):
        def realtime_overview(self):
            raise ShopPrivateError("实时成交统计超时")

    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=FailingSalesClient(),
        ),
    ):
        failed = service.refresh_store_metrics(store)

    assert failed["gmv_today"] == 16092
    assert failed["_sales_is_stale"] is True
    assert failed["_sales_attempt_status"]["today"] == "failed"
    assert "微信小店实时成交统计" in failed["_sales_source"]
    assert "实时成交统计超时" in failed["_sales_error"]


def test_official_sales_keeps_real_zero_and_partial_fields(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-sales-zero.db")
    store_id = database.add_store("零成交店铺", "wx-sales-zero", "secret")
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(database)

    class ZeroSalesClient(_MetricsPrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 0,
                "pay_order_cnt": 0,
                "pay_uv": 0,
            }

        def period_overview(self, days=1, offset_days=1):
            return {"pay_gmv": 0}

    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=ZeroSalesClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["gmv_today"] == 0
    assert metrics["orders_today"] == 0
    assert metrics["buyers_today"] == 0
    assert metrics["_sales_attempt_status"]["today"] == "updated"
    assert metrics["_sales_attempt_status"]["yesterday"] == "partial"
    assert metrics["_sales_is_stale"] is True


def test_official_sales_are_isolated_between_stores(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "official-sales-stores.db")
    first_id = database.add_store("成交店铺一", "wx-sales-first", "secret")
    second_id = database.add_store("成交店铺二", "wx-sales-second", "secret")
    service = SyncService(database)

    class PerStoreSalesClient(_MetricsPrivateClient):
        def __init__(self, _database, store):
            self.base = 10000 + store.id

        def realtime_overview(self):
            return {
                "pay_gmv": self.base,
                "pay_order_cnt": 2,
                "pay_uv": 1,
            }

        def period_overview(self, days=1, offset_days=1):
            return {
                "pay_gmv": self.base * days,
                "pay_order_cnt": days,
                "pay_uv": days,
            }

    with (
        patch.object(service, "validate_store_credential", return_value=None),
        patch.object(service, "_ensure_today_orders_cached", return_value=False),
        patch.object(service, "_analytics_days_for_metrics", return_value=[]),
        patch.object(service, "fetch_product_audit_quota", return_value={}),
        patch.object(service, "client_for", side_effect=ValueError("web-only")),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            PerStoreSalesClient,
        ),
    ):
        first = service.refresh_store_metrics(
            database.get_store(first_id)
        )
        second = service.refresh_store_metrics(
            database.get_store(second_id)
        )

    assert first["gmv_today"] == 10000 + first_id
    assert second["gmv_today"] == 10000 + second_id
    assert first["gmv_today"] != second["gmv_today"]
