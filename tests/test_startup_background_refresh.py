from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


def _controller(tmp_path: Path) -> NextUiController:
    database = Database(tmp_path / "startup.db")
    for index in range(2):
        store_id = database.add_store(
            f"启动刷新店铺{index}",
            f"wx-startup-{index}",
            "secret",
        )
        database.update_web_status(store_id, "已登录", "")
    return NextUiController(database)


def test_startup_refresh_runs_background_domains_once(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    calls: list[str] = []

    def violation(_payload):
        calls.append("violations")
        return {
            "succeeded": 2,
            "errors": [],
            "cloud_counterfeit": {"saved": 0},
        }

    def store_refresh(_payload):
        calls.append("stores")
        job_id = "store-job"
        controller._store_jobs[job_id] = {"status": "completed"}
        return {"job_id": job_id}

    def order_refresh(_payload):
        calls.append("orders")
        job_id = "order-job"
        controller._order_jobs[job_id] = {
            "status": "completed",
            "updated": 434,
            "status_changes": [{"order_id": "changed"}],
            "orders": [{"order_id": f"bulk-{index}"} for index in range(500)],
        }
        return {"job_id": job_id}

    def product_refresh(payload):
        calls.append(f"products:{payload['store_id']}")
        job_id = f"product-{payload['store_id']}"
        controller._product_jobs[job_id] = {"status": "completed"}
        return {"job_id": job_id}

    with (
        mock.patch.object(controller, "_refresh_violations", side_effect=violation),
        mock.patch.object(
            controller,
            "_start_store_refresh",
            side_effect=store_refresh,
        ),
        mock.patch.object(
            controller,
            "_start_order_quick_refresh",
            side_effect=order_refresh,
        ),
        mock.patch.object(
            controller,
            "_start_product_refresh",
            side_effect=product_refresh,
        ),
    ):
        result = controller.dispatch(
            "startup.refresh.start",
            {"enabled": {}},
        )
        controller._startup_refresh_thread.join(5)

    assert result["started"] is True
    assert result["already_running"] is False
    assert calls[:1] == ["violations"]
    assert calls[1:] == [
        "stores",
        "orders",
        "products:1",
        "products:2",
    ]
    status = controller.dispatch("startup.refresh.status", {})
    assert status["status"] == "completed"
    assert status["run_id"] == result["run_id"]
    assert status["stages"]["stores"]["status"] == "completed"
    assert status["stages"]["orders"]["status"] == "completed"
    assert status["stages"]["orders"]["result"]["updated"] == 434
    assert status["stages"]["orders"]["result"]["status_change_count"] == 1
    assert "orders" not in status["stages"]["orders"]["result"]
    assert status["thread_alive"] is False


def test_startup_refresh_respects_disabled_switches(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    with (
        mock.patch.object(controller, "_refresh_violations") as violations,
        mock.patch.object(controller, "_start_store_refresh") as stores,
        mock.patch.object(controller, "_start_order_quick_refresh") as orders,
        mock.patch.object(controller, "_start_product_refresh") as products,
    ):
        controller.dispatch(
            "startup.refresh.start",
            {
                "enabled": {
                    "stores": False,
                    "orders": False,
                    "products": False,
                    "violations": False,
                }
            },
        )
        controller._startup_refresh_thread.join(5)

    violations.assert_not_called()
    stores.assert_not_called()
    orders.assert_not_called()
    products.assert_not_called()
    status = controller.dispatch("startup.refresh.status", {})
    assert status["status"] == "completed"


def test_startup_violation_refresh_runs_once_for_all_real_stores(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    calls: list[dict] = []

    def violation(payload):
        calls.append(dict(payload))
        return {
            "refreshed": 2,
            "succeeded": 2,
            "errors": [],
            "cloud_counterfeit": {"saved": 1},
        }

    with (
        mock.patch.object(
            controller,
            "_refresh_violations",
            side_effect=violation,
        ),
        mock.patch.object(controller, "_start_store_refresh"),
        mock.patch.object(controller, "_start_order_quick_refresh"),
        mock.patch.object(controller, "_start_product_refresh"),
    ):
        controller.dispatch(
            "startup.refresh.start",
            {
                "enabled": {
                    "stores": False,
                    "orders": False,
                    "products": False,
                    "violations": True,
                }
            },
        )
        controller._startup_refresh_thread.join(5)

    assert calls == [{}]
    status = controller.dispatch("startup.refresh.status", {})
    assert status["stages"]["violations"]["status"] == "completed"


def test_startup_violation_refresh_fails_without_real_login(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "startup.db")
    demo_id = database.add_store("演示店铺", "demo-store", "local")
    database.update_web_status(demo_id, "已登录", "")
    controller = NextUiController(database)

    with mock.patch.object(
        controller,
        "db",
        database,
    ):
        controller.dispatch(
            "startup.refresh.start",
            {
                "enabled": {
                    "stores": False,
                    "orders": False,
                    "products": False,
                    "violations": True,
                }
            },
        )
        controller._startup_refresh_thread.join(5)

    status = controller.dispatch("startup.refresh.status", {})
    assert status["stages"]["violations"]["status"] == "failed"
    assert status["status"] == "failed"
    assert "没有可同步的已登录真实店铺" in str(
        status["stages"]["violations"]["result"]["errors"]
    )


def test_startup_violation_cloud_failure_is_not_reported_complete(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)

    with (
        mock.patch.object(
            controller,
            "_refresh_violations",
            return_value={
                "refreshed": 2,
                "succeeded": 2,
                "errors": [],
                "cloud_counterfeit": {
                    "error": "HTTP 404",
                    "saved": 0,
                },
            },
        ),
        mock.patch.object(controller, "_start_store_refresh"),
        mock.patch.object(controller, "_start_order_quick_refresh"),
        mock.patch.object(controller, "_start_product_refresh"),
    ):
        controller.dispatch(
            "startup.refresh.start",
            {
                "enabled": {
                    "stores": False,
                    "orders": False,
                    "products": False,
                    "violations": True,
                }
            },
        )
        controller._startup_refresh_thread.join(5)

    status = controller.dispatch("startup.refresh.status", {})
    stage = status["stages"]["violations"]
    assert stage["status"] == "failed"
    assert "云端" in str(stage["result"]["errors"])


def test_startup_order_refresh_runs_without_store_management_stage(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    order_refreshes: list[dict] = []

    with (
        mock.patch.object(controller, "_refresh_violations") as violations,
        mock.patch.object(controller, "_start_store_refresh") as stores,
        mock.patch.object(
            controller,
            "_start_order_quick_refresh",
            side_effect=lambda payload: (
                order_refreshes.append(dict(payload)),
                {"job_id": "order-job"},
            )[1],
        ),
        mock.patch.object(controller, "_start_product_refresh") as products,
        mock.patch.object(
            controller,
            "_wait_refresh_job",
            return_value={"status": "completed", "succeeded": 2},
        ),
    ):
        controller.dispatch(
            "startup.refresh.start",
            {
                "enabled": {
                    "stores": False,
                    "orders": True,
                    "products": False,
                    "violations": False,
                }
            },
        )
        controller._startup_refresh_thread.join(5)

    violations.assert_not_called()
    stores.assert_not_called()
    products.assert_not_called()
    assert order_refreshes == [{}]
    status = controller.dispatch("startup.refresh.status", {})
    assert status["stages"]["orders"]["status"] == "completed"
    assert status["stages"]["orders"]["result"]["succeeded"] == 2


def test_new_store_followup_merges_batch_and_runs_each_domain(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    stores = controller.db.list_stores()
    calls: list[tuple[str, int]] = []

    with (
        mock.patch.object(
            controller,
            "_refresh_violations",
            side_effect=lambda payload: (
                calls.append(
                    ("violations", int(payload["store_id"]))
                ),
                {
                    "succeeded": 1,
                    "errors": [],
                    "cloud_counterfeit": {"saved": 0},
                },
            )[1],
        ),
        mock.patch.object(
            controller,
            "_start_store_refresh",
            side_effect=lambda payload: (
                calls.append(("stores", int(payload["store_ids"][0]))),
                {"job_id": "s"},
            )[1],
        ),
        mock.patch.object(
            controller,
            "_start_order_quick_refresh",
            side_effect=lambda payload: (
                calls.append(("orders", int(payload["store_id"]))),
                {"job_id": "o"},
            )[1],
        ),
        mock.patch.object(
            controller,
            "_start_product_refresh",
            side_effect=lambda payload: (
                calls.append(("products", int(payload["store_id"]))),
                {"job_id": "p"},
            )[1],
        ),
        mock.patch.object(
            controller,
            "_wait_refresh_job",
            return_value={"status": "completed", "message": "ok"},
        ),
    ):
        result = controller._schedule_new_store_followup(
            [store.id for store in stores],
            reason="批量登录完成",
        )
        deadline = time.time() + 5
        while (
            controller._new_store_followup_thread is not None
            and time.time() < deadline
        ):
            time.sleep(0.01)

    assert result["scheduled"] is True
    assert calls.count(("violations", stores[0].id)) == 1
    assert calls.count(("violations", stores[1].id)) == 1
    assert calls.count(("stores", stores[0].id)) == 1
    assert calls.count(("stores", stores[1].id)) == 1
    assert calls.count(("orders", stores[0].id)) == 1
    assert calls.count(("orders", stores[1].id)) == 1
    assert calls.count(("products", stores[0].id)) == 1
    assert calls.count(("products", stores[1].id)) == 1


def test_new_store_followup_reports_cloud_violation_failure(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    store = controller.db.list_stores()[0]

    with (
        mock.patch.object(
            controller,
            "_refresh_violations",
            return_value={
                "succeeded": 1,
                "errors": [],
                "cloud_counterfeit": {
                    "saved": 0,
                    "error": "HTTP 404",
                },
            },
        ),
        mock.patch.object(
            controller,
            "_start_store_refresh",
            return_value={"job_id": "s"},
        ),
        mock.patch.object(
            controller,
            "_start_order_quick_refresh",
            return_value={"job_id": "o"},
        ),
        mock.patch.object(
            controller,
            "_start_product_refresh",
            return_value={"job_id": "p"},
        ),
        mock.patch.object(
            controller,
            "_wait_refresh_job",
            return_value={"status": "completed"},
        ),
    ):
        controller._schedule_new_store_followup(
            [store.id],
            reason="真实店铺登录完成",
        )
        deadline = time.time() + 5
        while (
            controller._new_store_followup_thread is not None
            and time.time() < deadline
        ):
            time.sleep(0.01)

    status = json.loads(
        controller.db.get_setting(
            "new_store_followup_status",
            "{}",
        )
    )
    assert status["status"] == "partial"
    assert status["stores"][0]["violations"].startswith("failed:")
    assert "HTTP 404" in status["stores"][0]["violations"]


def test_startup_refresh_rejects_parallel_duplicate(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking(_payload):
        entered.set()
        release.wait(3)
        return {}

    controller._refresh_violations = blocking
    controller._start_store_refresh = mock.Mock(
        return_value={"job_id": "s"}
    )
    controller._start_order_quick_refresh = mock.Mock(
        return_value={"job_id": "o"}
    )
    controller._start_product_refresh = mock.Mock(
        return_value={"job_id": "p"}
    )
    controller._store_jobs["s"] = {"status": "completed"}
    controller._order_jobs["o"] = {"status": "completed"}
    controller._product_jobs["p"] = {"status": "completed"}
    first = controller.dispatch("startup.refresh.start", {})
    assert entered.wait(2)
    second = controller.dispatch("startup.refresh.start", {})
    release.set()
    controller._startup_refresh_thread.join(5)

    assert first["started"] is True
    assert second["started"] is False
    assert second["already_running"] is True
    assert second["run_id"] == first["run_id"]
