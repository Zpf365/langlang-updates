from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from weimi_manager.activity_renewal import ActivityAutoRenewalManager
from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=SHANGHAI)


class FakeSnapupService:
    def __init__(
        self,
        activities_by_store,
        *,
        missing_create_readback=False,
        create_fail_store_ids=(),
    ):
        self.activities_by_store = {
            store_id: [dict(row) for row in rows]
            for store_id, rows in activities_by_store.items()
        }
        self.scan_calls = []
        self.create_calls = []
        self.missing_create_readback = missing_create_readback
        self.create_fail_store_ids = set(create_fail_store_ids)

    def snapup_activities(self, store):
        self.scan_calls.append(store.id)
        return [dict(row) for row in self.activities_by_store.get(store.id, [])]

    def create_snapup_activity(
        self,
        store,
        product_ids,
        discount,
        *,
        start_time,
        end_time,
        min_discount_cents,
        limit_purchase_num,
        stock_limit_per_sku,
        create_all,
    ):
        self.create_calls.append(
            {
                "store_id": store.id,
                "product_ids": list(product_ids),
                "discount": float(discount),
                "start_time": int(start_time),
                "end_time": int(end_time),
                "min_discount_cents": int(min_discount_cents),
                "limit_purchase_num": int(limit_purchase_num),
                "stock_limit_per_sku": int(stock_limit_per_sku),
                "create_all": bool(create_all),
            }
        )
        if store.id in self.create_fail_store_ids:
            raise RuntimeError("官方创建失败")
        activity_id = f"auto-{store.id}-{len(self.create_calls)}"
        if not self.missing_create_readback:
            self.activities_by_store.setdefault(store.id, []).append(
                {
                    "activity_id": activity_id,
                    "status": 0,
                    "start_time": int(start_time),
                    "end_time": int(end_time),
                    "extra": {
                        "snapupProducts": [
                            {"productId": str(product_id)}
                            for product_id in product_ids
                        ]
                    },
                }
            )
        return {
            "activity_id": activity_id,
            "activity_count": 1,
            "product_count": len(product_ids),
            "start_time": int(start_time),
            "end_time": int(end_time),
            "already_active_count": 0,
        }


class FakeWebSession:
    def __init__(self, service, *, fail_store_ids=(), slow=False):
        self.service = service
        self.fail_store_ids = set(fail_store_ids)
        self.calls = []
        self.slow = slow
        self.release = threading.Event()
        self.entered = threading.Event()

    def renew_snapup_activity(
        self,
        store,
        activity_id,
        start_time,
        days,
    ):
        self.calls.append(
            (store.id, str(activity_id), int(start_time), int(days))
        )
        if self.slow:
            self.entered.set()
            self.release.wait(3)
        if store.id in self.fail_store_ids:
            raise RuntimeError("官方创建失败")
        new_id = f"new-{store.id}-{len(self.calls)}"
        end_time = int(start_time) + int(days) * 86400
        self.service.activities_by_store.setdefault(store.id, []).append(
            {
                "activity_id": new_id,
                "title": "续期活动",
                "status": 0,
                "start_time": int(start_time),
                "end_time": end_time,
            }
        )
        return {
            "status": "created",
            "source_activity_id": str(activity_id),
            "new_activity_id": new_id,
            "store_id": store.id,
            "start_time": int(start_time),
            "end_time": end_time,
            "days": int(days),
        }


def _manager(
    tmp_path: Path,
    activities_builder,
    *,
    fail_store_ids=(),
    fail_first: bool = False,
):
    database = Database(tmp_path / "renewal.db")
    store_a = database.add_store("店铺 A", "wx-renew-a", "secret")
    store_b = database.add_store("店铺 B", "wx-renew-b", "secret")
    for store_id in (store_a, store_b):
        database.update_web_status(store_id, "已登录", "")
        database.save_activity_renewal_config(
            store_id,
            enabled=1,
            hours_before=6,
            duration_days=7,
        )
    database.set_setting("activity_auto_renewal_enabled", "1")
    database.set_setting("activity_auto_renewal_interval_minutes", "10")
    activities = activities_builder(store_a, store_b)
    if fail_first:
        fail_store_ids = {store_a}
    service = FakeSnapupService(activities)
    session = FakeWebSession(service, fail_store_ids=fail_store_ids)
    manager = ActivityAutoRenewalManager(
        database,
        service,
        session,
        now_provider=lambda: NOW,
    )
    return database, manager, service, session, store_a, store_b


def _seed_new_product(
    database: Database,
    store_id: int,
    product_id: str,
    *,
    first_seen: datetime,
    status: int = 5,
    stock: int = 10,
) -> None:
    from weimi_manager.models import Product

    database.upsert_product(
        Product(
            store_id=store_id,
            store_name=f"店铺 {store_id}",
            product_id=product_id,
            title=f"今日新品 {product_id}",
            status=status,
            status_text="出售中" if status == 5 else "已下架",
            edit_status=0,
            min_price=1990,
            sku_count=1,
            stock_total=stock,
            spu_code="",
            out_product_id="",
            head_img="https://img.alicdn.com/main.jpg",
            raw={
                "skus": [
                    {
                        "sku_id": f"{product_id}-SKU",
                        "sale_price": 1990,
                        "stock_num": stock,
                    }
                ]
            },
        )
    )
    with database.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO product_catalog (
                store_id, product_id, state, attempts, last_error,
                first_seen_at, last_seen_at, detail_synced_at,
                last_scan_id
            ) VALUES (?, ?, 'done', 0, '', ?, ?, ?, 'test')
            """,
            (
                store_id,
                product_id,
                first_seen.astimezone(timezone.utc).isoformat(timespec="seconds"),
                first_seen.astimezone(timezone.utc).isoformat(timespec="seconds"),
                first_seen.astimezone(timezone.utc).isoformat(timespec="seconds"),
            ),
        )


def _auto_manager(
    tmp_path: Path,
    *,
    missing_readback: bool = False,
    fail_store_ids=(),
) -> tuple[Database, ActivityAutoRenewalManager, FakeSnapupService, int]:
    database = Database(tmp_path / "smart-activity.db")
    store_id = database.add_store("智能活动店", "wx-smart", "secret")
    database.update_web_status(store_id, "已登录", "")
    database.save_activity_renewal_config(
        store_id,
        enabled=1,
        hours_before=6,
        duration_days=7,
    )
    database.set_setting("activity_auto_renewal_enabled", "1")
    service = FakeSnapupService(
        {store_id: []},
        missing_create_readback=missing_readback,
        create_fail_store_ids=fail_store_ids,
    )
    manager = ActivityAutoRenewalManager(
        database,
        service,
        FakeWebSession(service),
        now_provider=lambda: NOW,
    )
    manager.save_auto_create_rules(
        {
            "start_delay_minutes": 5,
            "duration_seconds": 7 * 86400,
            "discount": 0.85,
            "min_discount_cents": 100,
            "stock_limit_per_sku": 0,
            "limit_purchase_num": 0,
        }
    )
    return database, manager, service, store_id


def test_auto_renewal_confirms_official_new_activity_and_is_idempotent(
    tmp_path: Path,
) -> None:
    source_end = int((NOW + timedelta(hours=2)).timestamp())
    database, manager, service, session, store_a, _store_b = _manager(
        tmp_path,
        lambda store_a, _store_b: {
            store_a: [
                {
                    "activity_id": "source-a",
                    "title": "商家限时活动",
                    "status": 1,
                    "start_time": source_end - 86400,
                    "end_time": source_end,
                }
            ]
        },
    )

    waiting = manager.run_due(
        store_ids=[store_a],
        force=True,
        now=NOW,
    )
    after_end = datetime.fromtimestamp(
        source_end + 1,
        SHANGHAI,
    )
    first = manager.run_due(
        store_ids=[store_a],
        force=True,
        now=after_end,
    )
    second = manager.run_due(
        store_ids=[store_a],
        force=True,
        now=after_end,
    )

    assert waiting["stores"][0]["status"] == "waiting_for_end"
    assert first["stores"][0]["status"] == "success"
    assert second["stores"][0]["status"] == "success"
    assert len(session.calls) == 1
    run = database.get_activity_renewal_run(
        store_a,
        "source-a",
        source_end,
    )
    assert run is not None
    assert run["status"] == "success"
    assert run["new_activity_id"].startswith("new-")
    assert run["new_start_time"] == source_end + 61
    assert run["new_end_time"] == source_end + 61 + 7 * 86400


def test_auto_renewal_restart_restores_idempotent_success(tmp_path: Path) -> None:
    source_end = int((NOW + timedelta(hours=1)).timestamp())
    database, manager, service, session, store_a, _store_b = _manager(
        tmp_path,
        lambda store_a, _store_b: {
            store_a: [
                {
                    "activity_id": "source-restart",
                    "status": 1,
                    "end_time": source_end,
                }
            ]
        },
    )
    after_end = datetime.fromtimestamp(
        source_end + 1,
        SHANGHAI,
    )
    manager.run_due(
        store_ids=[store_a],
        force=True,
        now=after_end,
    )

    restored = ActivityAutoRenewalManager(
        database,
        service,
        session,
        now_provider=lambda: NOW,
    )
    result = restored.run_due(
        store_ids=[store_a],
        force=True,
        now=after_end,
    )

    assert result["stores"][0]["status"] == "success"
    assert len(session.calls) == 1


def test_auto_renewal_treats_naive_clock_as_asia_shanghai(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "renewal-timezone.db")
    manager = ActivityAutoRenewalManager(
        database,
        FakeSnapupService({}),
        FakeWebSession(FakeSnapupService({})),
        now_provider=lambda: datetime(2026, 11, 23, 0, 30),
    )

    current = manager._now()

    assert current.utcoffset() == timedelta(hours=8)
    assert current.hour == 0


def test_auto_renewal_isolates_store_failure_and_handles_milliseconds(
    tmp_path: Path,
) -> None:
    source_a = int((NOW - timedelta(minutes=5)).timestamp())
    source_b = int((NOW - timedelta(minutes=4)).timestamp())
    database, manager, _service, session, store_a, store_b = _manager(
        tmp_path,
        lambda store_a, store_b: {
            store_a: [
                {
                    "activity_id": "source-fail",
                    "status": 2,
                    "end_time": source_a * 1000,
                }
            ],
            store_b: [
                {
                    "activity_id": "source-ok",
                    "status": 2,
                    "endTime": source_b,
                }
            ],
        },
        fail_first=True,
    )

    result = manager.run_due(
        store_ids=[store_a, store_b],
        force=True,
        now=NOW,
    )

    by_store = {row["store_id"]: row for row in result["stores"]}
    assert by_store[store_a]["status"] in {"failed", "verification_pending"}
    assert by_store[store_b]["status"] == "success"
    assert len(session.calls) == 2
    failed = database.get_activity_renewal_run(
        store_a,
        "source-fail",
        source_a,
    )
    assert failed is not None
    assert failed["error"]
    assert failed["next_retry_at"] > int(NOW.timestamp())


def test_auto_renewal_concurrency_lock_prevents_duplicate_creation(
    tmp_path: Path,
) -> None:
    source_end = int((NOW - timedelta(minutes=5)).timestamp())
    database, manager, service, session, store_a, _store_b = _manager(
        tmp_path,
        lambda store_a, _store_b: {
            store_a: [
                {
                    "activity_id": "source-concurrent",
                    "status": 2,
                    "end_time": source_end,
                }
            ]
        },
    )
    session.slow = True
    store = database.get_store(store_a)
    results = []

    first = threading.Thread(
        target=lambda: results.append(
            manager.check_store(store, force=True, now=NOW)
        )
    )
    first.start()
    assert session.entered.wait(2)
    second = manager.check_store(store, force=True, now=NOW)
    session.release.set()
    first.join(3)

    assert second["status"] == "running"
    assert len(session.calls) == 1
    assert results[0]["status"] == "success"


def test_auto_renewal_ignores_platform_and_non_window_activities(
    tmp_path: Path,
) -> None:
    source_end = int((NOW - timedelta(minutes=5)).timestamp())
    database, manager, _service, session, store_a, _store_b = _manager(
        tmp_path,
        lambda store_a, _store_b: {
            store_a: [
                {
                    "activity_id": "platform-linked",
                    "platformActivityId": "1323",
                    "status": 2,
                    "end_time": source_end,
                },
                {
                    "activity_id": "far-away",
                    "status": 1,
                    "end_time": source_end + 24 * 3600,
                },
                {
                    "activity_id": "expired-long-ago",
                    "status": 1,
                    "end_time": source_end - 10 * 86400,
                },
            ]
        },
    )

    result = manager.run_due(store_ids=[store_a], force=True, now=NOW)

    assert result["stores"][0]["status"] == "idle"
    assert session.calls == []


def test_auto_renewal_can_recover_a_just_ended_activity(
    tmp_path: Path,
) -> None:
    source_end = int((NOW - timedelta(minutes=5)).timestamp())
    _database, manager, _service, session, store_a, _store_b = _manager(
        tmp_path,
        lambda store_a, _store_b: {
            store_a: [
                {
                    "activity_id": "source-just-ended",
                    "title": "跨日结束商家活动",
                    "status": 2,
                    "start_time": source_end - 86400,
                    "end_time": source_end,
                }
            ]
        },
    )

    result = manager.run_due(
        store_ids=[store_a],
        force=True,
        now=NOW,
    )

    assert result["stores"][0]["status"] == "success"
    assert len(session.calls) == 1
    assert session.calls[0][2] >= int(NOW.timestamp()) + 60


def test_auto_renewal_does_not_confirm_a_different_same_window_activity(
    tmp_path: Path,
) -> None:
    source_end = int((NOW - timedelta(minutes=5)).timestamp())
    _database, manager, _service, session, store_a, _store_b = _manager(
        tmp_path,
        lambda store_a, _store_b: {
            store_a: [
                {
                    "activity_id": "source-a",
                    "status": 2,
                    "end_time": source_end,
                }
            ]
        },
    )

    def wrong_id_renewal(
        _store,
        _activity_id,
        start_time,
        days,
    ):
        session.calls.append(
            (_store.id, "source-a", int(start_time), int(days))
        )
        session.service.activities_by_store[_store.id].append(
            {
                "activity_id": "unrelated-activity",
                "status": 0,
                "start_time": int(start_time),
                "end_time": int(start_time) + int(days) * 86400,
            }
        )
        return {
            "status": "created",
            "new_activity_id": "expected-activity",
            "start_time": int(start_time),
            "end_time": int(start_time) + int(days) * 86400,
        }

    session.renew_snapup_activity = wrong_id_renewal
    result = manager.run_due(
        store_ids=[store_a],
        force=True,
        now=NOW,
    )

    assert result["stores"][0]["status"] == "verification_pending"
    assert result["stores"][0]["attempts"] == 1


def test_controller_renewal_rpc_persists_config_and_runs_real_scan(
    tmp_path: Path,
) -> None:
    source_end = int((NOW - timedelta(minutes=5)).timestamp())
    database = Database(tmp_path / "renewal-rpc.db")
    store_id = database.add_store("续期店铺", "wx-renew-rpc", "secret")
    database.update_web_status(store_id, "已登录", "")
    service = FakeSnapupService(
        {
            store_id: [
                {
                    "activity_id": "source-rpc",
                    "status": 2,
                    "end_time": source_end,
                }
            ]
        }
    )
    session = FakeWebSession(service)
    controller = NextUiController(
        database,
        product_service=service,
        web_session=session,
        now_provider=lambda: NOW,
    )

    saved = controller.dispatch(
        "activity.renewal.save",
        {
            "enabled": True,
            "interval_minutes": 10,
            "stores": [
                {
                    "store_id": store_id,
                    "enabled": True,
                    "hours_before": 6,
                    "duration_days": 7,
                }
            ],
        },
    )
    assert saved["enabled"] is True
    assert saved["interval_minutes"] == 10
    assert saved["stores"][0]["enabled"] == 1

    started = controller.dispatch(
        "activity.renewal.scan.start",
        {"store_ids": [store_id]},
    )
    deadline = time.time() + 5

    job = {}
    while time.time() < deadline:
        job = controller.dispatch(
            "activity.renewal.scan.status",
            {"job_id": started["job_id"]},
        )
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert job["status"] == "completed"
    assert job["results"][0]["status"] == "success"
    assert len(session.calls) == 1
    status = controller.dispatch("activity.renewal.status", {})
    assert status["runs"][0]["source_activity_id"] == "source-rpc"


def test_smart_automation_defaults_off_and_restart_restores_enabled(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "smart-default.db")
    store_id = database.add_store("默认店", "wx-default-auto", "secret")
    database.update_web_status(store_id, "已登录", "")
    database.save_activity_renewal_config(store_id, enabled=1)
    service = FakeSnapupService({store_id: []})
    manager = ActivityAutoRenewalManager(
        database,
        service,
        FakeWebSession(service),
        now_provider=lambda: NOW,
    )
    assert manager.snapshot()["enabled"] is False
    manager.save_global_config(enabled=True, interval_minutes=10)
    restored = ActivityAutoRenewalManager(
        database,
        service,
        FakeWebSession(service),
        now_provider=lambda: NOW,
    )
    assert restored.snapshot()["enabled"] is True
    assert restored.snapshot()["stores"][0]["enabled"] == 1


def test_today_new_product_is_auto_created_and_confirmed_once(
    tmp_path: Path,
) -> None:
    database, manager, service, store_id = _auto_manager(tmp_path)
    _seed_new_product(
        database,
        store_id,
        "NEW-1",
        first_seen=NOW,
    )
    first = manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW,
    )
    second = manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW,
    )
    assert first["stores"][0]["auto_create"]["outcomes"][0]["status"] == "success"
    assert second["stores"][0]["auto_create"]["processed"] == 0
    assert len(service.create_calls) == 1
    runs = database.list_activity_auto_create_runs(store_id)
    assert runs[0]["status"] == "success"
    assert runs[0]["activity_id"].startswith("auto-")
    assert runs[0]["start_time"] >= int(NOW.timestamp()) + 300
    assert runs[0]["end_time"] - runs[0]["start_time"] == 7 * 86400


def test_yesterday_and_invalid_products_are_not_created(
    tmp_path: Path,
) -> None:
    database, manager, service, store_id = _auto_manager(tmp_path)
    _seed_new_product(
        database,
        store_id,
        "OLD-1",
        first_seen=NOW - timedelta(days=1),
    )
    _seed_new_product(
        database,
        store_id,
        "ZERO-STOCK",
        first_seen=NOW,
        stock=0,
    )
    _seed_new_product(
        database,
        store_id,
        "OFFLINE",
        first_seen=NOW,
        status=6,
    )
    service.activities_by_store[store_id].append(
        {
            "activity_id": "existing",
            "status": 1,
            "start_time": int(NOW.timestamp()),
            "end_time": int((NOW + timedelta(days=1)).timestamp()),
            "extra": {
                "snapupProducts": [{"productId": "EXISTING"}]
            },
        }
    )
    _seed_new_product(
        database,
        store_id,
        "EXISTING",
        first_seen=NOW,
    )
    result = manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW,
    )
    assert service.create_calls == []
    outcomes = result["stores"][0]["auto_create"]["outcomes"]
    assert {
        row["product_id"]: row["status"]
        for row in outcomes
    } == {
        "ZERO-STOCK": "skipped",
        "OFFLINE": "skipped",
        "EXISTING": "skipped",
    }


def test_auto_create_missing_readback_enters_verification_pending(
    tmp_path: Path,
) -> None:
    database, manager, service, store_id = _auto_manager(
        tmp_path,
        missing_readback=True,
    )
    _seed_new_product(
        database,
        store_id,
        "VERIFY-1",
        first_seen=NOW,
    )
    result = manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW,
    )
    row = result["stores"][0]["auto_create"]["outcomes"][0]
    assert row["status"] == "verification_pending"
    assert "未确认新活动生效" in row["error"]
    assert len(service.create_calls) == 1


def test_auto_create_per_scan_limit_queues_remaining_products(
    tmp_path: Path,
) -> None:
    database, manager, service, store_id = _auto_manager(tmp_path)
    for index in range(4):
        _seed_new_product(
            database,
            store_id,
            f"LIMIT-{index}",
            first_seen=NOW,
        )
    first = manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW,
    )
    assert len(service.create_calls) == 3
    assert first["stores"][0]["auto_create"]["processed"] == 3
    second = manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW,
    )
    assert len(service.create_calls) == 4
    assert second["stores"][0]["auto_create"]["processed"] == 1


def test_auto_create_store_failure_is_isolated(tmp_path: Path) -> None:
    database = Database(tmp_path / "smart-store-failure.db")
    store_a = database.add_store("失败店", "wx-fail-auto", "secret")
    store_b = database.add_store("成功店", "wx-ok-auto", "secret")
    for store_id in (store_a, store_b):
        database.update_web_status(store_id, "已登录", "")
        database.save_activity_renewal_config(store_id, enabled=1)
        _seed_new_product(
            database,
            store_id,
            f"P-{store_id}",
            first_seen=NOW,
        )
    database.set_setting("activity_auto_renewal_enabled", "1")
    service = FakeSnapupService(
        {store_a: [], store_b: []},
        create_fail_store_ids={store_a},
    )
    manager = ActivityAutoRenewalManager(
        database,
        service,
        FakeWebSession(service),
        now_provider=lambda: NOW,
    )
    manager.save_auto_create_rules(
        {
            "start_delay_minutes": 5,
            "duration_seconds": 86400,
            "discount": 0.85,
            "min_discount_cents": 100,
            "stock_limit_per_sku": 0,
            "limit_purchase_num": 0,
        }
    )
    result = manager.run_due(force=True, now=NOW)
    by_store = {row["store_id"]: row for row in result["stores"]}
    assert by_store[store_a]["auto_create"]["outcomes"][0]["status"] == "failed"
    assert by_store[store_b]["auto_create"]["outcomes"][0]["status"] == "success"


def _wait_cloud_status(
    database: Database,
    expected: set[str],
) -> str:
    deadline = time.time() + 3
    current = ""
    while time.time() < deadline:
        try:
            current = json.loads(
                database.get_setting(
                    "activity_snapup_auto_cloud_status",
                    "{}",
                )
            ).get("status", "")
        except Exception:
            current = ""
        if current in expected:
            return current
        time.sleep(0.02)
    return current


def _controller_for_cloud(tmp_path: Path, client) -> NextUiController:
    database = Database(tmp_path / f"cloud-{id(client)}.db")
    store_id = database.add_store("云端托管店", "wx-cloud-auto", "secret")
    database.update_web_status(store_id, "已登录", "")
    service = FakeSnapupService({store_id: []})
    context = SimpleNamespace(
        account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
        username="zpfly",
        offline=False,
        member_role="owner",
        permissions={},
        entitlements={},
        tokens=SimpleNamespace(access_token="test-token"),
    )
    return NextUiController(
        database,
        product_service=service,
        web_session=FakeWebSession(service),
        auth_context=context,
        auth_client=client,
        now_provider=lambda: NOW,
    )


def test_cloud_save_failure_keeps_local_switch_and_reports_pending_status(
    tmp_path: Path,
) -> None:
    class Client:
        def upsert_organization_settings(self, _token, _items):
            raise RuntimeError("cloud unavailable")

    controller = _controller_for_cloud(tmp_path, Client())
    saved = controller.dispatch(
        "activity.renewal.save",
        {
            "enabled": True,
            "interval_minutes": 10,
            "stores": [
                {
                    "store_id": controller.db.list_stores()[0].id,
                    "enabled": True,
                    "hours_before": 6,
                    "duration_days": 7,
                }
            ],
        },
    )
    assert saved["enabled"] is True
    assert _wait_cloud_status(
        controller.db,
        {"failed"},
    ) == "failed"
    assert controller.activity_renewal_manager.global_enabled() is True


def test_cloud_setting_restores_switch_and_rules_on_restart(
    tmp_path: Path,
) -> None:
    cloud_payload = {
        "schema_version": 1,
        "enabled": True,
        "interval_minutes": 12,
        "rules": {
            "start_delay_minutes": 8,
            "duration_seconds": 2 * 86400,
            "discount": 0.82,
            "min_discount_cents": 200,
            "stock_limit_per_sku": 0,
            "limit_purchase_num": 2,
        },
        "stores": [],
    }

    class Client:
        def pull_organization_settings(self, _token):
            return [
                {
                    "key": "activity_smart_automation",
                    "value": json.dumps(cloud_payload),
                }
            ]

    controller = _controller_for_cloud(tmp_path, Client())
    controller._restore_activity_smart_cloud_settings()
    assert _wait_cloud_status(
        controller.db,
        {"synced"},
    ) == "synced"
    snapshot = controller.activity_renewal_manager.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["interval_minutes"] == 12
    assert snapshot["auto_create_rules"]["discount"] == 0.82
    assert snapshot["auto_create_rules"]["saved_at"]


def test_manual_stop_prevents_future_auto_create(tmp_path: Path) -> None:
    database, manager, service, store_id = _auto_manager(tmp_path)
    _seed_new_product(database, store_id, "STOP-1", first_seen=NOW)
    first = manager.run_due(store_ids=[store_id], force=True, now=NOW)
    run = first["stores"][0]["auto_create"]["outcomes"][0]
    stopped = manager.stop_auto_create(int(run["id"]))
    assert stopped["status"] == "manual_stopped"
    services_before = len(service.create_calls)
    manager.run_due(store_ids=[store_id], force=True, now=NOW)
    assert len(service.create_calls) == services_before


def test_auto_create_stops_after_retry_limit(tmp_path: Path) -> None:
    database, manager, service, store_id = _auto_manager(
        tmp_path,
        fail_store_ids={1},
    )
    _seed_new_product(database, store_id, "RETRY-1", first_seen=NOW)
    for hours in (0, 1, 3):
        manager.run_due(
            store_ids=[store_id],
            force=True,
            now=NOW + timedelta(hours=hours),
        )
    assert len(service.create_calls) == 3
    manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW + timedelta(hours=4),
    )
    assert len(service.create_calls) == 3
    runs = database.list_activity_auto_create_runs(store_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["attempts"] == 3
    assert runs[0]["next_retry_at"] == 0


def test_auto_create_daily_limit_leaves_product_queued(tmp_path: Path) -> None:
    database, manager, service, store_id = _auto_manager(tmp_path)
    version = manager.auto_create_rules()["rule_version"]
    for index in range(20):
        database.save_activity_auto_create_run(
            store_id,
            f"PRIOR-{index}",
            NOW.date().isoformat(),
            version,
            status="success",
            attempts=1,
            activity_id=f"act-{index}",
            last_checked_at=int(NOW.timestamp()),
        )
    _seed_new_product(database, store_id, "LIMIT-PENDING", first_seen=NOW)
    result = manager.run_due(
        store_ids=[store_id],
        force=True,
        now=NOW,
    )
    assert result["stores"][0]["auto_create"]["status"] == "daily_limit"
    assert service.create_calls == []
    assert database.get_activity_auto_create_run(
        store_id,
        "LIMIT-PENDING",
        NOW.date().isoformat(),
        version,
    ) is None
