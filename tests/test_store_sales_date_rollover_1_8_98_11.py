from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from weimi_manager.db import Database
from weimi_manager.service import SyncService


class _PrivateClient:
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
        return 0

    def recommend_overview(self, *_args, **_kwargs):
        return {}

    def search_overview(self, _days):
        return {"search_exposure_yesterday": 0}

    def audit_success_today(self):
        return 0

    def status_counts(self):
        return {}


def test_new_day_does_not_reuse_previous_day_today_sales(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "sales-rollover.db")
    store_id = database.add_store(
        "再百宽栾商行",
        "wx-rollover",
        "secret",
    )
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "_cache_store_id": store_id,
                "_cache_store_app_id": "wx-rollover",
                "_sales_snapshot_date": "2026-11-25",
                "_sales_updated_at": "2026-11-25 23:59:00",
                "_sales_period_sources": {
                    "today": "微信小店实时成交统计",
                    "yesterday": "官方昨日",
                    "seven_days": "官方7日",
                    "thirty_days": "官方30日",
                },
                "_sales_attempt_status": {
                    "today": "updated",
                    "yesterday": "updated",
                    "seven_days": "updated",
                    "thirty_days": "updated",
                },
                "gmv_today": 1078,
                "today_orders": 1,
                "gmv_yesterday": 1078,
                "orders_yesterday": 1,
                "gmv_7d": 1078,
                "orders_7d": 1,
                "gmv_30d": 1078,
                "orders_30d": 1,
            },
            ensure_ascii=False,
        ),
    )
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(
        database,
        now_provider=lambda: datetime(2026, 11, 26, 9, 0, 0),
    )

    with (
        patch.object(
            service,
            "validate_store_credential",
            return_value=None,
        ),
        patch.object(
            service,
            "_ensure_today_orders_cached",
            return_value=False,
        ),
        patch.object(
            service,
            "_analytics_days_for_metrics",
            return_value=[],
        ),
        patch.object(
            service,
            "fetch_product_audit_quota",
            return_value={},
        ),
        patch.object(
            service,
            "client_for",
            side_effect=ValueError("web-only"),
        ),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=_PrivateClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["gmv_today"] == 0
    assert metrics["today_orders"] == 0
    assert metrics["_sales_snapshot_date"] == "2026-11-25"
    assert metrics["_sales_is_stale"] is True


def test_new_day_does_not_reuse_previous_day_refund_amount(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "refund-rollover.db")
    store_id = database.add_store(
        "跨日退款店铺",
        "wx-refund-rollover",
        "secret",
    )
    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "_cache_store_id": store_id,
                "_cache_store_app_id": "wx-refund-rollover",
                "_sales_snapshot_date": "2026-11-25",
                "_sales_updated_at": "2026-11-25 23:59:00",
                "_sales_period_sources": {
                    "today": "微信小店实时成交统计",
                },
                "_sales_attempt_status": {"today": "updated"},
                "gmv_today": 1078,
                "today_orders": 1,
                "refund_gmv_today": 900,
                "_refund_period_sources": {
                    "today": "微信小店实时成交统计",
                },
                "_refund_attempt_status": {"today": "updated"},
                "_refund_updated_at": "2026-11-25 23:59:00",
            },
            ensure_ascii=False,
        ),
    )
    store = database.get_store(store_id)
    assert store is not None
    service = SyncService(
        database,
        now_provider=lambda: datetime(2026, 11, 26, 9, 0, 0),
    )

    class NewSalesWithoutRefundClient(_PrivateClient):
        def realtime_overview(self):
            return {
                "pay_gmv": 2000,
                "pay_order_cnt": 2,
                "pay_uv": 2,
            }

        def period_overview(self, days=1, offset_days=1):
            return {
                "pay_gmv": 1000,
                "pay_order_cnt": 1,
                "pay_uv": 1,
            }

        def product_overview(self, *, days=1, offset_days=0, realtime=False):
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

        def status_counts(self):
            return {}

    with (
        patch.object(
            service,
            "validate_store_credential",
            return_value=None,
        ),
        patch.object(
            service,
            "_ensure_today_orders_cached",
            return_value=False,
        ),
        patch.object(
            service,
            "_analytics_days_for_metrics",
            return_value=[],
        ),
        patch.object(
            service,
            "fetch_product_audit_quota",
            return_value={},
        ),
        patch.object(
            service,
            "client_for",
            side_effect=ValueError("web-only"),
        ),
        patch(
            "weimi_manager.service.ShopPrivateClient",
            return_value=NewSalesWithoutRefundClient(),
        ),
    ):
        metrics = service.refresh_store_metrics(store)

    assert metrics["gmv_today"] == 2000
    assert metrics["today_orders"] == 2
    assert metrics["refund_gmv_today"] is None
    assert metrics["_refund_attempt_status"]["today"] == "missing"
    assert "today" not in metrics["_refund_period_sources"]
    assert metrics["_sales_snapshot_date"] == "2026-11-26"
