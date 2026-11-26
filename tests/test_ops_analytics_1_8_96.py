from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from weimi_manager.analytics import (
    AnalyticsApiClient,
    AnalyticsConfig,
    AnalyticsOutbox,
    AnalyticsReporter,
    AnalyticsRuntime,
    AnalyticsTransportError,
    REPORT_ENDPOINTS,
    registry,
)
from weimi_manager.analytics.adapter import AnalyticsRegistry
from weimi_manager.analytics.builders import (
    product_daily_payloads,
    store_daily_payloads,
)
from weimi_manager.analytics.modules import register_builtin_adapters
from weimi_manager.auth_config import AuthConfig
from weimi_manager.db import Database
from weimi_manager.models import Order, OrderItem, Product
from weimi_manager.webui.controller import (
    ANALYTICS_PUBLISH_KEY_LIMIT,
    MORE_NAV,
    PRIMARY_NAV,
    NextUiController,
)


class _Response:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


def _event(module: str = "orders", event_type: str = "order.created"):
    register_builtin_adapters()
    return registry.get(module).build_event(
        event_type,
        {
            "order_ref": "ORDER-1",
            "status": "待发货",
            "amount": 9900,
            "item_count": 1,
            "created_at": "2026-11-25T12:00:00+08:00",
            "paid_at": "2026-11-25T12:01:00+08:00",
            "after_sale": False,
            "source_tab": "orders",
            "currency_code": "CNY",
            "buyer_name": "不应出现",
            "phone": "13800000000",
            "address": "不应出现",
            "merchant_notes": "不应出现",
            "app_secret": "secret",
            "cookie": "cookie",
            "token": "token",
        },
        device_ref="device-1",
        store_ref="1",
        batch_id="batch-1",
        occurred_at="2026-11-25T12:02:00+08:00",
    )


def test_every_business_module_has_an_analytics_adapter() -> None:
    register_builtin_adapters()
    required = {
        key for key, *_rest in PRIMARY_NAV
    } | {
        key for key, *_rest in MORE_NAV
    }
    assert registry.validate_module_coverage(required)
    for key in required:
        assert registry.get(key).event_types()

    future_registry = AnalyticsRegistry()
    future_registry.register(registry.get("stores"))
    with pytest.raises(
        AssertionError,
        match="future_module",
    ):
        future_registry.validate_module_coverage(
            {"stores", "future_module"}
        )


def test_reporter_contract_covers_all_versioned_endpoints() -> None:
    assert set(REPORT_ENDPOINTS) == {
        "heartbeat",
        "store-daily",
        "product-daily",
        "order-events",
        "procurement-events",
        "upload-events",
        "error-summary",
    }
    assert all(path.startswith("/v1/report/") for path in REPORT_ENDPOINTS.values())


def test_controller_exposes_configuration_and_internal_diagnostics_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "rpc.db")
    controller = NextUiController(database, account_label="analytics-test")
    with pytest.raises(ValueError, match="不支持"):
        controller.dispatch(
            "analytics.status",
            {},
        )
    monkeypatch.setenv("LANGLANG_ANALYTICS_DEV_RPC", "1")
    controller = NextUiController(database, account_label="analytics-test")
    configured = controller.dispatch(
        "analytics.config.save",
        {"enabled": False, "base_url": ""},
    )
    assert configured["config"]["enabled"] is False
    assert configured["config"]["base_url"] == ""
    enabled = controller.dispatch(
        "analytics.config.save",
        {
            "enabled": True,
            "base_url": "https://ops.example.test",
        },
    )
    assert enabled["config"]["enabled"] is True
    controller.stop_analytics_reporter()
    status = controller.dispatch("analytics.status", {})
    assert "modules" in status
    assert "outbox" in status
    assert "events" in controller.dispatch(
        "analytics.diagnostics",
        {"limit": 10},
    )


def test_webui_derives_default_ops_report_dispatcher_from_auth_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGLANG_ANALYTICS_BASE_URL", raising=False)
    monkeypatch.delenv("LANGLANG_ANALYTICS_ENABLED", raising=False)
    database = Database(tmp_path / "derived-ops-report.db")
    auth_config = AuthConfig(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="public-client-key",
    )
    controller = NextUiController(
        database,
        account_label="analytics-default",
        auth_client=SimpleNamespace(config=auth_config),
    )
    try:
        assert controller.analytics.config.enabled is True
        assert controller.analytics.config.base_url == (
            "https://project-ref.supabase.co/functions/v1/ops-report"
        )
        assert controller.analytics.reporter.api_client.dispatcher_mode is True
    finally:
        controller.stop_analytics_reporter()

    database.set_setting("ops_analytics_enabled", "0")
    disabled = NextUiController(
        database,
        account_label="analytics-disabled",
        auth_client=SimpleNamespace(config=auth_config),
    )
    try:
        assert disabled.analytics.config.enabled is False
    finally:
        disabled.stop_analytics_reporter()


@pytest.mark.parametrize(
    "category",
    ["timeout", "server_error", "malformed_response"],
)
def test_default_dispatcher_failures_never_escape_business_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    monkeypatch.delenv("LANGLANG_ANALYTICS_BASE_URL", raising=False)
    database = Database(tmp_path / f"dispatch-{category}.db")
    auth_config = AuthConfig(
        supabase_url="https://project-ref.supabase.co",
        publishable_key="public-client-key",
    )
    controller = NextUiController(
        database,
        account_label="analytics-failure",
        auth_client=SimpleNamespace(config=auth_config),
    )

    class FailingApi:
        dispatcher_mode = True

        def send(self, *_args, **_kwargs):
            raise AnalyticsTransportError(category)

    controller.analytics.reporter.api_client = FailingApi()
    try:
        result = controller.dispatch("bootstrap", {})
        assert result["brand"]["name"] == "澜琅"
    finally:
        controller.stop_analytics_reporter()


def test_startup_refresh_and_dispatch_share_observed_idempotent_publisher(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "startup-analytics.db")
    successful_store = database.add_store(
        "启动成功店铺",
        "wx-startup-ok",
        "secret",
    )
    failed_store = database.add_store(
        "启动失败店铺",
        "wx-startup-failed",
        "secret",
    )
    database.update_web_status(successful_store, "已登录", "")
    database.update_web_status(failed_store, "已登录", "")
    database.set_setting(
        f"store_metrics_{successful_store}",
        json.dumps(
            {
                "_cache_store_id": successful_store,
                "_sales_attempt_status": {"today": "updated"},
                "_sales_period_sources": {"today": "启动刷新官方结果"},
                "_sales_updated_at": "2026-11-25 12:00:00",
                "_sales_is_stale": False,
                "gmv_today": 0,
                "today_orders": 0,
                "refund_gmv_today": 0,
            }
        ),
    )
    for store_id, product_id in (
        (successful_store, "STARTUP-P-OK"),
        (failed_store, "STARTUP-P-FAIL"),
    ):
        database.upsert_product(
            Product(
                store_id,
                "店铺",
                product_id,
                f"商品 {product_id}",
                5,
                "出售中",
                0,
                1990,
                1,
                5,
                "",
                "",
                "",
                {},
            )
        )
        database.upsert_product_metrics(
            store_id,
            product_id,
            sold30=1,
        )
    now = int(datetime.now().timestamp())
    for store_id, order_id in (
        (successful_store, "STARTUP-ORDER-OK"),
        (failed_store, "STARTUP-ORDER-FAIL"),
    ):
        database.upsert_order(
            Order(
                store_id=store_id,
                store_name="店铺",
                order_id=order_id,
                status=20,
                status_text="待发货",
                create_time=now,
                update_time=now,
                pay_time=now,
                total_amount=1990,
                product_amount=1990,
                freight_amount=0,
                discount_amount=0,
                buyer_name="",
                phone="",
                address="",
                waybill_id="",
                carrier="",
                merchant_notes="",
                items=[
                    OrderItem(
                        product_id=(
                            "STARTUP-P-OK"
                            if store_id == successful_store
                            else "STARTUP-P-FAIL"
                        ),
                        sku_id="SKU-1",
                        title="商品",
                        sku_count=1,
                        sale_price=1990,
                        sku_attributes="",
                    )
                ],
                raw={},
            )
        )

    class CapturingAnalytics:
        def __init__(self) -> None:
            self.events = []
            self.lock = threading.Lock()

        def emit(
            self,
            module_key,
            event_type,
            payload=None,
            *,
            store_ref="",
            observed=True,
            **_kwargs,
        ):
            if not observed:
                return False
            with self.lock:
                self.events.append(
                    {
                        "module": module_key,
                        "event_type": event_type,
                        "payload": dict(payload or {}),
                        "store_ref": str(store_ref),
                    }
                )
            return True

        def defer(self, task):
            task()
            return True

    controller = NextUiController(database)
    analytics = CapturingAnalytics()
    controller.analytics = analytics
    store_result = {
        "job_id": "startup-store-job",
        "status": "partial",
        "successful_store_ids": [successful_store],
        "failed_store_ids": [failed_store],
        "errors": ["失败店铺隔离"],
    }
    order_result = {
        "job_id": "startup-order-job",
        "status": "partial",
        "successful_store_ids": [successful_store],
        "failed_store_ids": [failed_store],
        "errors": ["失败店铺隔离"],
        "status_changes": [],
        "orders": [
            {
                "store_id": successful_store,
                "order_id": "STARTUP-ORDER-OK",
                "status_text": "待发货",
                "create_time": now,
                "pay_time": now,
                "total_amount": 1990,
            },
            {
                "store_id": failed_store,
                "order_id": "STARTUP-ORDER-FAIL",
                "status_text": "待发货",
                "create_time": now,
                "pay_time": now,
                "total_amount": 1990,
            },
        ],
    }

    with (
        mock.patch.object(
            controller,
            "_refresh_violations",
            return_value={"succeeded": 1},
        ),
        mock.patch.object(
            controller,
            "_start_store_refresh",
            return_value={"job_id": "startup-store-job"},
        ),
        mock.patch.object(
            controller,
            "_start_order_quick_refresh",
            return_value={"job_id": "startup-order-job"},
        ),
        mock.patch.object(
            controller,
            "_start_product_refresh",
            side_effect=lambda payload: {
                "job_id": f"startup-product-{payload['store_id']}"
            },
        ),
        mock.patch.object(
            controller,
            "_wait_refresh_job",
            side_effect=lambda _jobs, _lock, job_id: (
                store_result
                if job_id == "startup-store-job"
                else order_result
                if job_id == "startup-order-job"
                else {
                    "job_id": job_id,
                    "status": (
                        "completed"
                        if job_id.endswith(str(successful_store))
                        else "failed"
                    ),
                    "errors": (
                        []
                        if job_id.endswith(str(successful_store))
                        else ["失败店铺隔离"]
                    ),
                }
            ),
        ),
    ):
        started = controller.dispatch("startup.refresh.start", {})
        controller._startup_refresh_thread.join(5)
        controller._emit_dispatch_analytics_work(
            "stores.refresh.status",
            {"job_id": "startup-store-job"},
            store_result,
        )
        controller._emit_dispatch_analytics_work(
            "stores.refresh.status",
            {"job_id": "startup-store-job"},
            store_result,
        )

    assert started["started"] is True
    store_events = [
        event
        for event in analytics.events
        if event["event_type"] == "store.daily_metrics"
    ]
    assert store_events
    assert {event["store_ref"] for event in store_events} == {
        str(successful_store)
    }
    assert all(event["payload"]["gmv"] == 0 for event in store_events)
    assert all(
        event["payload"]["paid_orders"] == 0
        for event in store_events
    )
    product_events = [
        event
        for event in analytics.events
        if event["event_type"] == "product.qualified_snapshot"
    ]
    assert {
        event["store_ref"]
        for event in product_events
    } == {str(successful_store)}
    order_events = [
        event
        for event in analytics.events
        if event["event_type"] == "order.created"
    ]
    assert {event["store_ref"] for event in order_events} == {
        str(successful_store)
    }
    assert len(store_events) == 1


def test_analytics_publish_idempotency_cache_is_bounded(
    tmp_path: Path,
) -> None:
    controller = NextUiController(
        Database(tmp_path / "bounded-publish-keys.db")
    )
    for index in range(ANALYTICS_PUBLISH_KEY_LIMIT + 300):
        controller._publish_refresh_analytics(
            "orders",
            {},
            {
                "status": "completed",
                "orders": [],
                "status_changes": [],
            },
            publish_key=f"job-{index}",
        )

    keys = controller._analytics_publish_keys
    assert len(keys) == ANALYTICS_PUBLISH_KEY_LIMIT
    assert "job-0" not in keys
    assert f"job-{ANALYTICS_PUBLISH_KEY_LIMIT + 299}" in keys


def test_adapter_removes_credentials_and_buyer_privacy_before_persistence() -> None:
    event = _event()
    assert event is not None
    assert event.payload == {
        "order_ref": "ORDER-1",
        "status": "待发货",
        "amount": 9900,
        "item_count": 1,
        "created_at": "2026-11-25T12:00:00+08:00",
        "paid_at": "2026-11-25T12:01:00+08:00",
        "after_sale": False,
        "source_tab": "orders",
        "currency_code": "CNY",
    }
    serialized = json.dumps(event.payload, ensure_ascii=False).casefold()
    for forbidden in (
        "buyer_name",
        "phone",
        "address",
        "merchant_notes",
        "app_secret",
        "cookie",
        "token",
    ):
        assert forbidden not in serialized


def test_outbox_survives_restart_and_deduplicates_event(tmp_path: Path) -> None:
    database_path = tmp_path / "analytics.db"
    database = Database(database_path)
    outbox = AnalyticsOutbox(database)
    event = _event()
    assert event is not None
    assert outbox.enqueue(event) is True
    assert outbox.enqueue(event) is False
    with database.connect() as conn:
        protected = conn.execute(
            "SELECT payload_protected FROM analytics_outbox"
        ).fetchone()[0]
    assert b"ORDER-1" not in bytes(protected)
    assert b"13800000000" not in bytes(protected)

    reopened = Database(database_path)
    restored = AnalyticsOutbox(reopened)
    batch = restored.pending_batch()
    assert len(batch) == 1
    assert restored.payload(batch[0])["order_ref"] == "ORDER-1"
    diagnostics = restored.diagnostics()
    assert "payload" not in diagnostics[0]
    assert "ORDER-1" not in json.dumps(diagnostics, ensure_ascii=False)


def test_daily_latest_events_revision_merge_deliver_latest_value(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "daily-revision.db")
    outbox = AnalyticsOutbox(database)
    adapter = registry.get("stores")
    first = adapter.build_event(
        "store.daily_metrics",
        {
            "business_date": "2026-11-25",
            "gmv": 100,
            "paid_orders": 1,
        },
        device_ref="device-1",
        store_ref="1",
        occurred_at="2026-11-25T10:00:00+08:00",
    )
    second = adapter.build_event(
        "store.daily_metrics",
        {
            "business_date": "2026-11-25",
            "gmv": 200,
            "paid_orders": 2,
        },
        device_ref="device-1",
        store_ref="1",
        occurred_at="2026-11-25T11:00:00+08:00",
    )
    assert first is not None and second is not None
    assert outbox.enqueue(first)
    assert outbox.enqueue(second)
    pending = outbox.pending_batch()
    assert len(pending) == 1
    latest_payload = outbox.payload(pending[0])
    assert latest_payload["gmv"] == 200
    assert latest_payload["paid_orders"] == 2
    assert int(pending[0]["revision"]) == second.revision

    outbox.mark_sent([str(pending[0]["event_id"])])
    third = adapter.build_event(
        "store.daily_metrics",
        {
            "business_date": "2026-11-25",
            "gmv": 300,
            "paid_orders": 3,
        },
        device_ref="device-1",
        store_ref="1",
        occurred_at="2026-11-25T12:00:00+08:00",
    )
    assert third is not None
    assert outbox.enqueue(third)
    pending = outbox.pending_batch()
    assert len(pending) == 1
    assert outbox.payload(pending[0])["gmv"] == 300


def test_feature_usage_pending_counter_merges_and_keeps_cumulative_after_send(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "counter.db")
    outbox = AnalyticsOutbox(database)
    adapter = registry.get("stores")
    for index in range(3):
        event = adapter.build_event(
            "feature.usage_daily",
            {
                "feature_key": "stores.refresh",
                "use_count": 1,
                "task_count": index,
                "error_count": 0,
                "business_date": "2026-11-25",
            },
            device_ref="device-1",
            store_ref="1",
        )
        assert event is not None
        assert outbox.enqueue(event)
    pending = outbox.pending_batch()
    assert len(pending) == 1
    payload = outbox.payload(pending[0])
    assert payload["use_count"] == 3
    assert payload["task_count"] == 3
    outbox.mark_sent([str(pending[0]["event_id"])])

    fourth = adapter.build_event(
        "feature.usage_daily",
        {
            "feature_key": "stores.refresh",
            "use_count": 1,
            "task_count": 0,
            "error_count": 1,
            "business_date": "2026-11-25",
        },
        device_ref="device-1",
        store_ref="1",
    )
    assert fourth is not None
    assert outbox.enqueue(fourth)
    pending = outbox.pending_batch()
    assert len(pending) == 1
    payload = outbox.payload(pending[0])
    assert payload["use_count"] == 4
    assert payload["task_count"] == 3
    assert payload["error_count"] == 1


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, "auth_rejected"),
        (403, "auth_rejected"),
        (429, "rate_limited"),
        (500, "server_error"),
    ],
)
def test_api_client_maps_auth_rate_limit_and_server_failures(
    status: int,
    category: str,
) -> None:
    client = AnalyticsApiClient(
        "https://ops.example.test",
        lambda: "token",
        session=_Session(_Response(status, {"error": "x"})),
    )
    with pytest.raises(AnalyticsTransportError) as raised:
        client.send("order-events", [{"event_id": "e1"}], batch_id="b1")
    assert raised.value.category == category


def test_api_client_rejects_malformed_json_and_uses_bearer_token() -> None:
    session = _Session(_Response(200, ValueError("bad json")))
    client = AnalyticsApiClient(
        "https://ops.example.test",
        lambda: "signed-user-token",
        session=session,
    )
    with pytest.raises(AnalyticsTransportError) as raised:
        client.send("store-daily", [{"event_id": "e1"}], batch_id="b1")
    assert raised.value.category == "malformed_response"
    assert session.calls[0]["headers"]["Authorization"] == (
        "Bearer signed-user-token"
    )


def test_api_client_dispatcher_mode_posts_to_ops_report_once() -> None:
    session = _Session(_Response(200, {"accepted_event_ids": ["e1"]}))
    client = AnalyticsApiClient(
        "https://project-ref.supabase.co/functions/v1/ops-report/",
        lambda: "user-jwt",
        session=session,
    )
    result = client.send(
        "order-events",
        [
            {
                "event_id": "e1",
                "event_type": "order.created",
                "payload": {"order_ref": "ORDER-1"},
            }
        ],
        batch_id="batch-1",
    )

    assert result.accepted_event_ids == ("e1",)
    call = session.calls[0]
    assert call["url"] == (
        "https://project-ref.supabase.co/functions/v1/ops-report"
    )
    assert call["headers"]["Authorization"] == "Bearer user-jwt"
    assert call["json"]["batch_id"] == "batch-1"
    assert call["json"]["events"][0]["event_type"] == "order.created"


def test_api_client_separated_report_mode_keeps_legacy_endpoint() -> None:
    session = _Session(_Response(200, {"accepted_event_ids": ["e1"]}))
    client = AnalyticsApiClient(
        "https://ops.example.test",
        lambda: "user-jwt",
        session=session,
    )
    client.send("store-daily", [{"event_id": "e1"}], batch_id="batch-1")

    assert client.dispatcher_mode is False
    assert session.calls[0]["url"] == (
        "https://ops.example.test/v1/report/store-daily"
    )


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (requests.Timeout("slow"), "timeout"),
        (requests.ConnectionError("dns"), "network"),
    ],
)
def test_api_client_is_fail_safe_for_timeout_and_dns_failures(
    error: Exception,
    category: str,
) -> None:
    client = AnalyticsApiClient(
        "https://ops.example.test",
        lambda: "token",
        session=_Session(error=error),
    )
    with pytest.raises(AnalyticsTransportError) as raised:
        client.send("heartbeat", [{"event_id": "e1"}], batch_id="b1")
    assert raised.value.category == category


def test_reporter_batches_acknowledged_events_and_failure_is_isolated(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "report.db")
    event = _event()
    assert event is not None
    outbox = AnalyticsOutbox(database)
    outbox.enqueue(event)

    class SuccessApi:
        def send(self, endpoint, events, *, batch_id):
            assert endpoint == "order-events"
            return SimpleNamespace(
                accepted_event_ids=(event.event_id,),
                rejected_event_ids=(),
            )

    reporter = AnalyticsReporter(
        database,
        AnalyticsConfig(enabled=True),
        lambda: "token",
        api_client=SuccessApi(),
    )
    assert reporter.flush_once()["sent"] == 1
    assert outbox.stats()["by_status"]["sent"]["count"] == 1

    second = _event(event_type="order.status_changed")
    # The similar event has a different dedupe projection.
    second = registry.get("orders").build_event(
        "order.status_changed",
        {
            "order_ref": "ORDER-2",
            "from_status": "待付款",
            "to_status": "待发货",
        },
        device_ref="device-1",
        store_ref="1",
    )
    assert second is not None
    outbox.enqueue(second)

    class FailingApi:
        def send(self, *_args, **_kwargs):
            raise AnalyticsTransportError("timeout")

    failing = AnalyticsReporter(
        database,
        AnalyticsConfig(enabled=True),
        lambda: "token",
        api_client=FailingApi(),
    )
    result = failing.flush_once()
    assert result["released"] == 1
    assert failing.status()["last_error_category"] == "timeout"


def test_dispatcher_reporter_sends_one_mixed_event_batch(tmp_path: Path) -> None:
    database = Database(tmp_path / "dispatcher-batch.db")
    outbox = AnalyticsOutbox(database)
    order_event = _event()
    store_event = registry.get("stores").build_event(
        "store.daily_metrics",
        {
            "business_date": "2026-11-25",
            "gmv": 100,
            "paid_orders": 1,
        },
        device_ref="device-1",
        store_ref="1",
    )
    assert order_event is not None and store_event is not None
    outbox.enqueue(order_event)
    outbox.enqueue(store_event)
    calls = []

    class DispatcherApi:
        dispatcher_mode = True

        def send(self, endpoint, events, *, batch_id):
            calls.append((endpoint, events, batch_id))
            return SimpleNamespace(
                accepted_event_ids=tuple(
                    str(event["event_id"])
                    for event in events
                ),
                rejected_event_ids=(),
            )

    reporter = AnalyticsReporter(
        database,
        AnalyticsConfig(enabled=True),
        lambda: "token",
        api_client=DispatcherApi(),
    )

    assert reporter.flush_once()["sent"] == 2
    assert len(calls) == 1
    endpoint, events, _batch_id = calls[0]
    assert endpoint == ""
    assert {
        event["event_type"]
        for event in events
    } == {"order.created", "store.daily_metrics"}


def test_reporter_opens_circuit_breaker_after_repeated_failures(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "circuit.db")
    event = _event()
    assert event is not None
    AnalyticsOutbox(database).enqueue(event)

    class FailingApi:
        def send(self, *_args, **_kwargs):
            raise AnalyticsTransportError("server_error")

    reporter = AnalyticsReporter(
        database,
        AnalyticsConfig(enabled=True),
        lambda: "token",
        api_client=FailingApi(),
    )
    for _index in range(5):
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE analytics_outbox
                SET status='pending', next_attempt_at=?
                """,
                ("2000-01-01T00:00:00+00:00",),
            )
        reporter.flush_once()
    assert reporter.status()["circuit_open"] is True
    assert reporter.flush_once() == {"sent": 0, "released": 0}


def test_local_mock_http_auth_idempotency_and_offline_recovery(
    tmp_path: Path,
) -> None:
    state = {
        "fail": True,
        "events": {},
        "authorization": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if state["fail"]:
                self.send_response(503)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            state["authorization"].append(
                self.headers.get("Authorization")
            )
            accepted = []
            for event in body.get("events") or []:
                event_id = str(event.get("event_id") or "")
                if event_id and event_id not in state["events"]:
                    state["events"][event_id] = event
                accepted.append(event_id)
            payload = json.dumps(
                {
                    "accepted_event_ids": accepted,
                    "rejected_event_ids": [],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        database = Database(tmp_path / "mock-http.db")
        outbox = AnalyticsOutbox(database)
        event = _event()
        assert event is not None
        outbox.enqueue(event)
        reporter = AnalyticsReporter(
            database,
            AnalyticsConfig(enabled=True),
            lambda: "user-access-token",
            api_client=AnalyticsApiClient(
                f"http://127.0.0.1:{server.server_port}",
                lambda: "user-access-token",
            ),
        )
        first = reporter.flush_once()
        assert first["released"] == 1
        state["fail"] = False
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE analytics_outbox
                SET status='pending', next_attempt_at=?
                """,
                ("2000-01-01T00:00:00+00:00",),
            )
        second = reporter.flush_once()
        assert second["sent"] == 1
        assert len(state["events"]) == 1
        assert state["authorization"] == ["Bearer user-access-token"]
    finally:
        server.shutdown()
        server.server_close()


def test_local_mock_http_ops_report_dispatcher_path_and_jwt(
    tmp_path: Path,
) -> None:
    state = {"paths": [], "authorization": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            state["paths"].append(self.path)
            state["authorization"].append(
                self.headers.get("Authorization")
            )
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            accepted = [
                str(event.get("event_id") or "")
                for event in body.get("events") or []
                if event.get("event_id")
            ]
            payload = json.dumps(
                {
                    "accepted_event_ids": accepted,
                    "rejected_event_ids": [],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        database = Database(tmp_path / "dispatcher-http.db")
        event = _event()
        assert event is not None
        AnalyticsOutbox(database).enqueue(event)
        reporter = AnalyticsReporter(
            database,
            AnalyticsConfig(enabled=True),
            lambda: "jwt-user-token",
            api_client=AnalyticsApiClient(
                f"http://127.0.0.1:{server.server_port}/functions/v1/ops-report",
                lambda: "jwt-user-token",
            ),
        )
        assert reporter.flush_once()["sent"] == 1
        assert state["paths"] == ["/functions/v1/ops-report"]
        assert state["authorization"] == ["Bearer jwt-user-token"]
    finally:
        server.shutdown()
        server.server_close()


def test_reporter_singleton_and_single_thread_are_idempotent(
    tmp_path: Path,
) -> None:
    AnalyticsReporter.reset_singletons_for_tests()
    database = Database(tmp_path / "singleton.db")
    first = AnalyticsReporter.get_or_create(
        database,
        AnalyticsConfig(
            enabled=True,
            base_url="https://ops.example.test",
        ),
        lambda: "token",
    )
    second = AnalyticsReporter.get_or_create(
        database,
        AnalyticsConfig(
            enabled=True,
            base_url="https://ops.example.test",
        ),
        lambda: "token",
    )
    assert first is second
    first.start()
    first.start()
    assert first._thread is not None
    assert first._thread.is_alive()
    first.stop()


def test_outbox_batch_is_bounded_by_count_and_bytes(tmp_path: Path) -> None:
    database = Database(tmp_path / "batch.db")
    outbox = AnalyticsOutbox(database)
    adapter = registry.get("orders")
    for index in range(101):
        event = adapter.build_event(
            "order.created",
            {
                "order_ref": f"ORDER-{index}",
                "status": "待发货",
                "amount": index,
            },
            device_ref="device-1",
            store_ref="1",
        )
        assert event is not None
        assert outbox.enqueue(event)
    batch = outbox.pending_batch(limit=100, max_bytes=256 * 1024)
    assert len(batch) == 100
    assert sum(int(row["size_bytes"] or 0) for row in batch) <= 256 * 1024
    assert all(int(row["size_bytes"] or 0) < 5 * 1024 * 1024 for row in batch)


def test_product_filter_only_reports_qualified_products(tmp_path: Path) -> None:
    database = Database(tmp_path / "products.db")
    store_id = database.add_store("分析店铺", "wx-analytics", "secret")
    for product_id, title, image in (
        ("P-ORDINARY", "普通新品", ""),
        ("P-SOLD", "周期出单商品", "https://img.alicdn.com/main.jpg"),
        (
            "P-EXPOSURE",
            "高曝光商品",
            "data:image/png;base64,AAAA",
        ),
    ):
        database.upsert_product(
            Product(
                store_id,
                "分析店铺",
                product_id,
                title,
                5,
                "出售中",
                0,
                1990,
                1,
                10,
                "",
                "",
                image,
                {"category_id": "C1", "category_path": "服饰 / 帽子"},
            )
        )
    database.upsert_product_metrics(
        store_id,
        "P-SOLD",
        sold30=2,
        views30=20,
    )
    database.upsert_product_metrics(
        store_id,
        "P-EXPOSURE",
        sold30=0,
        recommend_views30=1200,
    )
    rows = product_daily_payloads(
        database,
        exposure_threshold=1000,
        now=datetime(2026, 11, 25, 12, 0, 0),
    )
    ids = {payload["product_id"] for _, payload in rows}
    assert ids == {"P-SOLD", "P-EXPOSURE"}
    by_id = {
        payload["product_id"]: payload
        for _, payload in rows
    }
    assert by_id["P-SOLD"]["thumbnail_url"].startswith("https://")
    assert by_id["P-SOLD"]["thumbnail_host"] == "img.alicdn.com"
    assert "thumbnail_url" not in by_id["P-EXPOSURE"]


def test_product_orders_and_units_are_counted_separately_and_delisted_sales_kept(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-orders.db")
    store_id = database.add_store("订单口径店铺", "wx-units", "secret")
    database.upsert_product(
        Product(
            store_id,
            "订单口径店铺",
            "P-DELISTED",
            "售出后已下架商品",
            6,
            "已下架",
            0,
            1990,
            2,
            0,
            "",
            "",
            "",
            {},
        )
    )
    current = datetime(2026, 11, 25, 12, 0, 0)
    stamp = int(current.timestamp())
    with database.connect() as conn:
        for order_id, amount in (("ORDER-A", 3980), ("ORDER-B", 5970)):
            conn.execute(
                """
                INSERT INTO orders (
                    store_id, order_id, status, status_text, create_time,
                    update_time, pay_time, total_amount, product_amount,
                    freight_amount, discount_amount, buyer_name, phone,
                    address, waybill_id, carrier, merchant_notes,
                    raw_json, synced_at
                ) VALUES (?, ?, 20, '待发货', ?, ?, ?, ?, ?, 0, 0,
                          '', '', '', '', '', '', '{}', '')
                """,
                (
                    store_id,
                    order_id,
                    stamp,
                    stamp,
                    stamp,
                    amount,
                    amount,
                ),
            )
        conn.executemany(
            """
            INSERT INTO order_items (
                store_id, order_id, product_id, sku_id, title,
                sku_count, sale_price, thumb_url, sku_attributes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')
            """,
            [
                (store_id, "ORDER-A", "P-DELISTED", "SKU-1", "商品", 1, 1990),
                (store_id, "ORDER-A", "P-DELISTED", "SKU-2", "商品", 1, 1990),
                (store_id, "ORDER-B", "P-DELISTED", "SKU-1", "商品", 3, 1990),
            ],
        )
    rows = product_daily_payloads(
        database,
        exposure_threshold=1000,
        now=current,
    )
    assert len(rows) == 1
    payload = rows[0][1]
    assert payload["paid_orders_today"] == 2
    assert payload["paid_units_today"] == 5
    assert payload["paid_orders"] == 2
    assert payload["sales_count"] == 5
    assert payload["sales_amount"] == 9950


def test_product_daily_includes_orders_created_yesterday_and_paid_today(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "product-yesterday-created.db")
    store_id = database.add_store("跨日订单店铺", "wx-cross-day", "secret")
    database.upsert_product(
        Product(
            store_id,
            "跨日订单店铺",
            "P-CROSS-DAY",
            "跨日支付商品",
            1,
            "已下架",
            0,
            1990,
            1,
            0,
            "",
            "",
            "",
            {},
        )
    )
    current = datetime(2026, 11, 25, 12, 0, 0)
    created = int((current - timedelta(days=1, hours=1)).timestamp())
    paid = int(current.timestamp())
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO orders (
                store_id, order_id, status, status_text, create_time,
                update_time, pay_time, total_amount, product_amount,
                freight_amount, discount_amount, buyer_name, phone,
                address, waybill_id, carrier, merchant_notes,
                raw_json, synced_at
            ) VALUES (?, 'ORDER-CROSS-DAY', 20, '待发货', ?, ?, ?, 1990,
                      1990, 0, 0, '', '', '', '', '', '', '{}', '')
            """,
            (store_id, created, paid, paid),
        )
        conn.execute(
            """
            INSERT INTO order_items (
                store_id, order_id, product_id, sku_id, title,
                sku_count, sale_price, thumb_url, sku_attributes
            ) VALUES (?, 'ORDER-CROSS-DAY', 'P-CROSS-DAY', 'SKU-1',
                      '跨日支付商品', 2, 1990, '', '')
            """,
            (store_id,),
        )

    rows = product_daily_payloads(
        database,
        exposure_threshold=1000,
        now=current,
    )

    assert len(rows) == 1
    payload = rows[0][1]
    assert payload["paid_orders_today"] == 1
    assert payload["paid_units_today"] == 2
    assert payload["sales_amount"] == 3980


def test_store_daily_does_not_fabricate_zero_without_successful_metrics(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-daily.db")
    store_id = database.add_store("新店铺", "wx-new-7009", "secret")
    rows = store_daily_payloads(
        database,
        now=datetime(2026, 11, 25, 12, 0, 0),
    )
    payload = rows[0][1]
    assert payload["_observed"] is False
    assert payload["gmv"] is None
    assert payload["paid_orders"] is None

    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "_cache_store_id": store_id,
                "_sales_attempt_status": {"today": "failed"},
                "_sales_period_sources": {},
                "_sales_updated_at": "",
                "gmv_today": 0,
                "today_orders": 0,
            }
        ),
    )
    failed = store_daily_payloads(database)[0][1]
    assert failed["_observed"] is False
    assert failed["gmv"] is None

    database.set_setting(
        f"store_metrics_{store_id}",
        json.dumps(
            {
                "_cache_store_id": store_id,
                "_sales_attempt_status": {"today": "updated"},
                "_sales_period_sources": {"today": "官方成交统计"},
                "_sales_updated_at": "2026-11-25 12:00:00",
                "_sales_is_stale": False,
                "gmv_today": 0,
                "today_orders": 0,
                "refund_gmv_today": 0,
            }
        ),
    )
    success_zero = store_daily_payloads(database)[0][1]
    assert success_zero["_observed"] is True
    assert success_zero["gmv"] == 0
    assert success_zero["paid_orders"] == 0
    assert success_zero["source"] == "官方成交统计"
    assert success_zero["store_name"] == "新店铺"
    assert success_zero["app_id_masked"].startswith("wx")
    assert success_zero["app_id_masked"] == "wx…7009"


def test_order_payload_supports_line_items_without_buyer_private_fields() -> None:
    adapter = registry.get("orders")
    event = adapter.build_event(
        "order.created",
        {
            "order_ref": "ORDER-LINES",
            "items": [
                {
                    "line_ref": "P1:SKU1",
                    "product_id": "P1",
                    "sku_ref": "SKU1",
                    "quantity": 2,
                    "sales_amount": 3980,
                    "title": "商品一",
                    "phone": "13800000000",
                    "address": "隐私地址",
                }
            ],
            "buyer_name": "隐私买家",
        },
        device_ref="device-1",
        store_ref="1",
    )
    assert event is not None
    assert event.payload["items"] == [
        {
            "line_ref": "P1:SKU1",
            "product_id": "P1",
            "sku_ref": "SKU1",
            "quantity": 2,
            "sales_amount": 3980,
            "title": "商品一",
        }
    ]
    assert "buyer_name" not in event.payload


def test_procurement_events_require_product_level_references() -> None:
    adapter = registry.get("procurement")
    for definition in adapter.event_types():
        if not definition.event_type.startswith("procurement."):
            continue
        if definition.event_type == "procurement.exception":
            assert "product_id" in definition.allowed_fields
            assert "sku_ref" in definition.allowed_fields
        for field in (
            "product_id",
            "sku_ref",
            "quantity",
            "store_order_item_ref",
        ):
            assert field in definition.allowed_fields


def test_disabled_or_broken_reporting_never_breaks_business_dispatch(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "fail-safe.db")
    controller = NextUiController(
        database,
        account_label="failure-test",
    )

    class FailingApi:
        def send(self, *_args, **_kwargs):
            raise AnalyticsTransportError("server_error")

    controller.analytics = AnalyticsRuntime(
        database,
        token_provider=lambda: "token",
        config=AnalyticsConfig(enabled=True),
    )
    controller.analytics.reporter.api_client = FailingApi()
    result = controller.dispatch("bootstrap", {})
    assert isinstance(result, dict)
    controller.analytics.stop()
    flush_result = controller.analytics.flush_now()
    assert flush_result["sent"] == 0
    assert not any(
        "analytics" in str(path).casefold()
        for path in (database.path.parent / "logs").glob("**/*")
        if path.is_file()
    )


def test_hot_path_event_projection_stays_below_five_milliseconds() -> None:
    adapter = registry.get("orders")
    payload = {
        "order_ref": "ORDER-PERF",
        "status": "待发货",
        "amount": 9900,
        "item_count": 1,
    }
    measurements = []
    for index in range(100):
        started = time.perf_counter()
        adapter.build_event(
            "order.created",
            payload,
            device_ref="device-1",
            store_ref="1",
        )
        measurements.append((time.perf_counter() - started) * 1000)
    assert sum(measurements) / len(measurements) < 5.0
    assert max(measurements) < 20.0


def test_business_dispatch_analytics_hook_is_offloaded_and_under_five_ms(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "hook.db")
    controller = NextUiController(database, account_label="hook-test")
    controller.analytics = AnalyticsRuntime(
        database,
        token_provider=lambda: "token",
        config=AnalyticsConfig(enabled=True),
    )
    measurements = []
    for _index in range(100):
        started = time.perf_counter()
        controller._emit_dispatch_analytics(
            "stores.login",
            {"store_ids": [1]},
            {"matched_store_ids": [1]},
        )
        measurements.append((time.perf_counter() - started) * 1000)
    controller.analytics.stop()
    assert sum(measurements) / len(measurements) < 5.0
    assert max(measurements) < 20.0


def test_analytics_payload_is_not_routed_to_user_visible_logs_or_ui() -> None:
    analytics_root = Path(__file__).resolve().parent.parent / "weimi_manager" / "analytics"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in analytics_root.glob("*.py")
    ).casefold()
    assert "runtime_logging" not in source
    assert "log_event(" not in source
    assert "manager.network" not in source
    assert "print(" not in source

    webui_source = (
        Path(__file__).resolve().parent.parent / "webui" / "src"
    )
    frontend = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in webui_source.rglob("*")
        if path.suffix in {".vue", ".js"}
    )
    for phrase in (
        "运营数据上传",
        "同步运营数据",
        "运营上报中",
        "运营上报成功",
    ):
        assert phrase not in frontend

    auth_text = (
        Path(__file__).resolve().parent.parent
        / "weimi_manager"
        / "pyside"
        / "auth_window.py"
    ).read_text(encoding="utf-8")
    assert "去标识化的店铺、商品、订单和任务统计" in auth_text
    assert "不上传账号密钥、Cookie、支付凭据或买家联系方式" in auth_text
