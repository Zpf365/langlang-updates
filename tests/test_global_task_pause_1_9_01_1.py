from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from weimi_manager.db import Database
from weimi_manager.pyside.pages.upload import UploadRunner
from weimi_manager.task_control import TaskPauseError
from weimi_manager.ui.upload_page import UploadPage as LegacyUploadPage
from weimi_manager.webui.controller import NextUiController


def _auth_context(account_id: str = "account-a") -> SimpleNamespace:
    return SimpleNamespace(
        account_id=account_id,
        username=account_id,
        email="",
        offline=False,
        organization_name="",
        member_role="owner",
        permissions={},
        entitlements={
            "account.view_others": True,
            "module.automation": True,
        },
        license=None,
        tokens=SimpleNamespace(access_token=f"token-{account_id}"),
    )


def _controller(
    database: Database,
    state_path: Path,
    *,
    account_id: str = "account-a",
    product_service=None,
) -> NextUiController:
    return NextUiController(
        database,
        auth_context=_auth_context(account_id),
        product_service=product_service,
        global_task_state_path=state_path,
    )


def _wait_for(predicate, *, timeout: float = 5.0, message: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


class _AccountService:
    def __init__(self) -> None:
        self.logout_calls: list[str] = []

    def logout(self, context, *, forget_local: bool = False) -> None:
        self.logout_calls.append(f"{context.account_id}:{forget_local}")


class _BatchProductService:
    def __init__(self) -> None:
        self.listing_calls: list[tuple[int, tuple[str, ...], bool]] = []

    def set_products_listing(self, store, product_ids, listed):
        self.listing_calls.append((store.id, tuple(product_ids), listed))
        return {"succeeded": len(product_ids), "failed": 0, "errors": []}


def test_global_pause_is_application_scoped_and_persists_across_accounts(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "global-task-control.json"
    first_database = Database(tmp_path / "account-a.db")
    service = _AccountService()
    first = _controller(first_database, state_path)
    first.auth_service = service
    first.account_action_handler = lambda _action: None

    paused = first.dispatch("tasks.pause-all", {})
    duplicate = first.dispatch("tasks.pause-all", {})

    assert paused["paused"] is True
    assert paused["changed"] is True
    assert paused["scope"] == "application"
    assert duplicate["changed"] is False
    assert duplicate["revision"] == paused["revision"]
    assert first.dispatch("bootstrap", {})["brand"]["name"] == "澜琅"
    assert first.dispatch("stores.list", {})["stores"] == []
    switched = first.dispatch("account.action", {"action": "switch"})
    assert switched["accepted"] is True
    assert service.logout_calls == []
    assert first.dispatch("tasks.status", {})["paused"] is True

    second_database = Database(tmp_path / "account-b.db")
    restarted = _controller(
        second_database,
        state_path,
        account_id="account-b",
    )
    status = restarted.dispatch("tasks.status", {})

    assert status["paused"] is True
    assert status["error"] == ""
    assert status["owner_account_id"] == "account-a"
    assert restarted.dispatch("bootstrap", {})["brand"]["name"] == "澜琅"
    resumed = restarted.dispatch("tasks.resume-all", {})
    duplicate_resume = restarted.dispatch("tasks.resume-all", {})
    assert resumed["paused"] is False
    assert resumed["changed"] is True
    assert duplicate_resume["changed"] is False
    assert duplicate_resume["revision"] == resumed["revision"]


def test_paused_product_batch_blocks_new_writes_and_resumes_after_release(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "global-task-control.json"
    database = Database(tmp_path / "product-batch.db")
    store_id = database.add_store("批量商品店铺", "wx-product-batch", "secret")
    service = _BatchProductService()
    controller = _controller(
        database,
        state_path,
        product_service=service,
    )

    controller.dispatch("tasks.pause-all", {})
    with pytest.raises(TaskPauseError, match="全局操作已暂停") as paused_error:
        controller.dispatch(
            "products.set-listing",
            {
                "store_id": store_id,
                "product_ids": ["product-1"],
                "listed": False,
            },
        )
    assert paused_error.value.code == "task_paused"
    assert service.listing_calls == []

    controller.dispatch("tasks.resume-all", {})
    result = controller.dispatch(
        "products.set-listing",
        {
            "store_id": store_id,
            "product_ids": ["product-1"],
            "listed": False,
        },
    )

    assert result["succeeded"] == 1
    assert service.listing_calls == [(store_id, ("product-1",), False)]


def test_product_batch_finishes_in_flight_item_then_waits_for_resume(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "global-task-control.json"
    database = Database(tmp_path / "active-product-batch.db")
    store_id = database.add_store(
        "在途商品店铺",
        "wx-active-product-batch",
        "secret",
    )
    first_started = threading.Event()
    release_first = threading.Event()

    class ProductService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def product_write(self, _store, _action, product_id, **_kwargs):
            self.calls.append(str(product_id))
            if len(self.calls) == 1:
                first_started.set()
                assert release_first.wait(5)
            return {"deleted": True}

    service = ProductService()
    controller = _controller(
        database,
        state_path,
        product_service=service,
    )
    first_result: dict = {}

    def run_first() -> None:
        first_result.update(
            controller.dispatch(
                "products.delete",
                {"store_id": store_id, "product_id": "product-1"},
            )
        )

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert first_started.wait(3)
    controller.dispatch("tasks.pause-all", {})
    release_first.set()
    first_thread.join(5)

    assert service.calls == ["product-1"]
    assert first_result["deleted"] is True
    with pytest.raises(TaskPauseError):
        controller.dispatch(
            "products.delete",
            {"store_id": store_id, "product_id": "product-2"},
        )
    assert service.calls == ["product-1"]

    controller.dispatch("tasks.resume-all", {})
    controller.dispatch(
        "products.delete",
        {"store_id": store_id, "product_id": "product-2"},
    )
    assert service.calls == ["product-1", "product-2"]


def test_upload_pause_finishes_in_flight_item_and_never_requeues_unknown(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "global-task-control.json"
    database = Database(tmp_path / "upload-pause.db")
    store_id = database.add_store("上传暂停店铺", "wx-upload-pause", "secret")
    first_started = threading.Event()
    release_first = threading.Event()

    class ProductService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def product_write(self, _store, _action, payload, **_kwargs):
            source_id = str(payload["product_id"])
            self.calls.append(source_id)
            if len(self.calls) == 1:
                first_started.set()
                assert release_first.wait(5)
                raise TimeoutError("platform response timed out")
            return {"product_id": f"remote-{source_id}"}

    service = ProductService()
    controller = _controller(
        database,
        state_path,
        product_service=service,
    )
    tasks = [
        {
            "id": "upload-task-1",
            "store_id": store_id,
            "store_name": "上传暂停店铺",
            "status": "pending",
            "template": {},
            "items": [
                {
                    "item_id": "item-1",
                    "source_product_id": "source-1",
                    "title": "商品一",
                    "status": "pending",
                },
                {
                    "item_id": "item-2",
                    "source_product_id": "source-2",
                    "title": "商品二",
                    "status": "pending",
                },
                {
                    "item_id": "item-unknown",
                    "source_product_id": "source-unknown",
                    "title": "结果未知商品",
                    "status": "verification_pending",
                    "message": "远端结果待核实",
                },
            ],
        }
    ]
    database.set_setting("upload_tasks", json.dumps(tasks, ensure_ascii=False))

    def upload_payload(_adapter, item, _template):
        return {
            "product_id": item["source_product_id"],
            "title": item["title"],
        }

    with mock.patch.object(
        LegacyUploadPage,
        "_upload_payload",
        staticmethod(upload_payload),
    ):
        started = controller.dispatch("upload.run.start", {})
        assert first_started.wait(3)
        before_pause = controller.dispatch("tasks.status", {})
        assert before_pause["running"] == 1
        assert before_pause["count"] == 1
        paused = controller.dispatch("tasks.pause-all", {})
        assert paused["paused"] is True
        release_first.set()
        _wait_for(
            lambda: controller.dispatch(
                "upload.run.status",
                {"job_id": started["job_id"]},
            )["status"] == "paused",
            message="upload job did not enter paused state",
        )
        time.sleep(0.1)
        assert service.calls == ["source-1"]
        assert paused["count"] == 1

        controller.dispatch("tasks.resume-all", {})
        _wait_for(
            lambda: controller.dispatch(
                "upload.run.status",
                {"job_id": started["job_id"]},
            )["status"]
            in {"completed", "partial", "failed"},
            message="upload job did not finish after resume",
        )

    assert service.calls == ["source-1", "source-2"]
    persisted = json.loads(database.get_setting("upload_tasks", "[]"))[0]
    item_statuses = {
        item["item_id"]: item["status"] for item in persisted["items"]
    }
    assert item_statuses == {
        "item-1": "verification_pending",
        "item-2": "success",
        "item-unknown": "verification_pending",
    }


def test_upload_pause_after_runner_return_cannot_revive_completed_job(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "global-task-control.json"
    database = Database(tmp_path / "upload-return-race.db")
    store_id = database.add_store(
        "上传收尾店铺",
        "wx-upload-return-race",
        "secret",
    )
    runner_returned = threading.Event()
    final_snapshot_blocked = threading.Event()
    release_final_snapshot = threading.Event()

    class ProductService:
        def product_write(self, _store, _action, payload, **_kwargs):
            return {"product_id": f"remote-{payload['product_id']}"}

    class FakeRunner:
        def __init__(self, workspace, tasks, *, progress=None, start_guard=None):
            self.workspace = workspace
            self.tasks = tasks
            self.progress = progress
            self.start_guard = start_guard
            self.paused = threading.Event()
            self.cancelled = threading.Event()

        def run(self, selected, *, selected_item_ids=None):
            item = selected[0]["items"][0]
            item["status"] = "success"
            item["product_id"] = "remote-return-race"
            selected[0]["status"] = "completed"
            self.workspace.db.set_setting(
                "upload_tasks",
                json.dumps(self.tasks, ensure_ascii=False),
            )
            runner_returned.set()
            return []

    controller = _controller(
        database,
        state_path,
        product_service=ProductService(),
    )
    database.set_setting(
        "upload_tasks",
        json.dumps(
            [
                {
                    "id": "upload-return-race",
                    "store_id": store_id,
                    "store_name": "上传收尾店铺",
                    "status": "pending",
                    "template": {},
                    "items": [
                        {
                            "item_id": "item-return-race",
                            "source_product_id": "source-return-race",
                            "title": "返回后暂停商品",
                            "status": "pending",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
    )
    original_upload_tasks = controller._upload_tasks

    def blocked_upload_tasks():
        values = original_upload_tasks()
        if runner_returned.is_set():
            final_snapshot_blocked.set()
            assert release_final_snapshot.wait(5)
        return values

    controller._upload_tasks = blocked_upload_tasks
    with mock.patch(
        "weimi_manager.pyside.pages.upload.UploadRunner",
        FakeRunner,
    ):
        started = controller.dispatch("upload.run.start", {})
        assert final_snapshot_blocked.wait(3)
        paused = controller.dispatch("tasks.pause-all", {})
        assert paused["paused"] is True
        release_final_snapshot.set()
        _wait_for(
            lambda: controller._upload_jobs[started["job_id"]]["status"]
            in {"completed", "partial", "failed"},
            message="completed upload result did not win over pause",
        )

        resumed = controller.dispatch("tasks.resume-all", {})
        assert resumed["paused"] is False
        _wait_for(
            lambda: controller._upload_jobs[started["job_id"]]["status"]
            in {"completed", "partial", "failed"},
            message="completed upload was revived as a fake running job",
        )

    job = controller._upload_jobs[started["job_id"]]
    assert job["status"] == "completed"
    assert job.get("paused_by_global_task") is not True


def test_store_refresh_stops_before_next_scan_and_resumes_remaining_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEIMI_STORE_REFRESH_WORKERS", "6")
    state_path = tmp_path / "global-task-control.json"
    database = Database(tmp_path / "store-pause.db")
    for index in range(9):
        database.add_store(
            f"刷新店铺{index}",
            f"wx-store-pause-{index}",
            "secret",
        )
    six_started = threading.Event()
    release = threading.Event()

    class StoreService:
        def __init__(self) -> None:
            self.refreshed: list[int] = []
            self.lock = threading.Lock()

        def refresh_store_metrics(self, store):
            with self.lock:
                self.refreshed.append(store.id)
                if len(self.refreshed) == 6:
                    six_started.set()
            if len(self.refreshed) <= 6:
                assert release.wait(5)
            return {}

    service = StoreService()
    controller = _controller(
        database,
        state_path,
        product_service=service,
    )

    started = controller.dispatch("stores.refresh.start", {})
    assert six_started.wait(3)
    controller.dispatch("tasks.pause-all", {})
    release.set()
    time.sleep(0.1)
    assert len(service.refreshed) == 6
    assert controller.dispatch(
        "stores.refresh.status",
        {"job_id": started["job_id"]},
    )["status"] == "paused"

    controller.dispatch("tasks.resume-all", {})
    controller.wait_for_store_refresh(started["job_id"], timeout=5)
    status = controller.dispatch(
        "stores.refresh.status",
        {"job_id": started["job_id"]},
    )

    assert status["status"] == "completed"
    assert sorted(service.refreshed) == sorted(
        store.id for store in database.list_stores()
    )


def test_global_pause_blocks_due_schedulers_and_waits_activity_renewal(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "scheduler-pause.json"
    database = Database(tmp_path / "scheduler-pause.db")
    store_id = database.add_store(
        "调度暂停店铺",
        "wx-scheduler-pause",
        "secret",
    )
    database.update_hosted_config(store_id, enabled=1)
    database.add_automation_task(
        "到期商品同步",
        "product_catalog",
        store_id,
        1,
    )
    now = datetime.now()
    database.set_setting("hosted_schedule_enabled", "1")
    database.set_setting("hosted_schedule_time", now.strftime("%H:%M"))
    database.set_setting("hosted_schedule_stores", json.dumps([store_id]))
    database.save_activity_renewal_config(store_id, enabled=1)
    calls: list[str] = []

    class Service:
        def snapup_activities(self, _store):
            calls.append("activity")
            return []

    controller = NextUiController(
        database,
        auth_context=_auth_context(),
        product_service=Service(),
        web_session=SimpleNamespace(),
        global_task_state_path=state_path,
    )
    controller.dispatch("tasks.pause-all", {})

    assert controller.run_due_automation_tasks(now)["started"] is False
    assert controller.run_due_hosted_schedule(now)["started"] is False
    assert controller._monitor_procurement_automation(
        force=True,
    )["reason"] == "global_pause"

    activity_result: list[dict] = []
    worker = threading.Thread(
        target=lambda: activity_result.append(
            controller.activity_renewal_manager.run_due(force=True)
        ),
        daemon=True,
    )
    worker.start()
    time.sleep(0.15)
    assert worker.is_alive()
    assert calls == []

    controller.dispatch("tasks.resume-all", {})
    worker.join(5)
    assert not worker.is_alive()
    assert activity_result[0]["status"] == "completed"
    assert calls == ["activity"]


def test_automation_job_waits_after_atomic_request_and_resumes_same_worker(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "automation-pause.json"
    database = Database(tmp_path / "automation-pause.db")
    first_store = database.add_store(
        "自动化一店",
        "wx-automation-one",
        "secret",
    )
    second_store = database.add_store(
        "自动化二店",
        "wx-automation-two",
        "secret",
    )
    first_task = database.add_automation_task(
        "自动化一店同步",
        "product_catalog",
        first_store,
        60,
    )
    second_task = database.add_automation_task(
        "自动化二店同步",
        "product_catalog",
        second_store,
        60,
    )
    entered = threading.Event()
    release = threading.Event()
    calls: list[tuple[int, str]] = []

    class Service:
        def scan_product_catalog(self, store):
            calls.append((store.id, threading.current_thread().name))
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
            return SimpleNamespace(message="ok")

    controller = _controller(
        database,
        state_path,
        product_service=Service(),
    )
    started = controller.dispatch(
        "automation.tasks.run-start",
        {"task_ids": [first_task, second_task]},
    )
    assert entered.wait(2)
    first_call_store = calls[0][0]
    controller.dispatch("tasks.pause-all", {})
    release.set()
    time.sleep(0.15)
    assert [store_id for store_id, _name in calls] == [first_call_store]
    assert controller._automation_jobs[started["job_id"]]["status"] == "paused"

    controller.dispatch("tasks.resume-all", {})
    _wait_for(
        lambda: controller._automation_jobs[started["job_id"]]["status"]
        == "completed",
        message="automation did not resume",
    )
    assert {store_id for store_id, _name in calls} == {
        first_store,
        second_store,
    }
    assert len({name for _store_id, name in calls}) == 1


def test_automation_job_does_not_continue_after_account_generation_change(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "automation-account.json"
    database = Database(tmp_path / "automation-account.db")
    first_store = database.add_store(
        "代次自动化一店",
        "wx-automation-generation-one",
        "secret",
    )
    second_store = database.add_store(
        "代次自动化二店",
        "wx-automation-generation-two",
        "secret",
    )
    first_task = database.add_automation_task(
        "代次一店",
        "product_catalog",
        first_store,
        60,
    )
    second_task = database.add_automation_task(
        "代次二店",
        "product_catalog",
        second_store,
        60,
    )
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    class Service:
        def scan_product_catalog(self, store):
            calls.append(store.id)
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
            return SimpleNamespace(message="ok")

    controller = _controller(
        database,
        state_path,
        product_service=Service(),
    )
    started = controller.dispatch(
        "automation.tasks.run-start",
        {"task_ids": [first_task, second_task]},
    )
    assert entered.wait(2)
    first_call_store = calls[0]
    controller._freeze_account_work("switch")
    release.set()
    _wait_for(
        lambda: controller._automation_jobs[started["job_id"]]["status"]
        == "paused",
        message="automation account pause was not retained",
    )
    assert calls == [first_call_store]
    assert controller._automation_jobs[started["job_id"]]["status"] == "paused"


def test_global_pause_state_error_is_visible_and_safe_operations_stay_available(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "global-task-control.json"
    state_path.write_text("{invalid-json", encoding="utf-8")
    database = Database(tmp_path / "state-error.db")
    service = _AccountService()
    controller = _controller(database, state_path)
    controller.auth_service = service
    controller.account_action_handler = lambda _action: None

    status = controller.dispatch("tasks.status", {})

    assert status["paused"] is None
    assert "读取" in status["error"]
    assert controller.dispatch("bootstrap", {})["brand"]["name"] == "澜琅"
    with pytest.raises(ValueError, match="暂停状态"):
        controller.dispatch("products.set-listing", {})
    switched = controller.dispatch("account.action", {"action": "logout"})
    assert switched["accepted"] is True
    assert service.logout_calls == ["account-a:True"]


def test_resume_never_touches_a_previous_account_generation_job(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "global-task-control.json"
    database = Database(tmp_path / "generation.db")
    controller = _controller(database, state_path)
    controller._upload_jobs["old-generation"] = {
        "job_id": "old-generation",
        "account_id": "account-a",
        "generation": controller._current_generation() - 1,
        "status": "paused",
        "paused_by_global_task": True,
        "previous_status": "running",
    }

    controller.dispatch("tasks.pause-all", {})
    controller.dispatch("tasks.resume-all", {})

    old_job = controller._upload_jobs["old-generation"]
    assert old_job["status"] == "paused"
    assert old_job["paused_by_global_task"] is True
