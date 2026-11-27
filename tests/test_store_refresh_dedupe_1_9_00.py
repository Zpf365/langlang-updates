from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


def test_overlapping_store_refresh_reuses_the_active_job(tmp_path: Path) -> None:
    database = Database(tmp_path / "store-refresh-dedupe.db")
    first_id = database.add_store("去重店铺一", "wx-dedupe-one", "secret")
    second_id = database.add_store("去重店铺二", "wx-dedupe-two", "secret")
    entered = threading.Event()
    release = threading.Event()

    class Service:
        def __init__(self) -> None:
            self.refreshed: list[int] = []
            self.scanned: list[int] = []

        def refresh_store_metrics(self, store):
            self.refreshed.append(store.id)
            entered.set()
            assert release.wait(3)
            return {}

        def scan_product_catalog(self, store):
            self.scanned.append(store.id)
            return None

    service = Service()
    controller = NextUiController(database, product_service=service)

    first = controller.dispatch("stores.refresh.start", {})
    assert entered.wait(2)
    second = controller.dispatch("stores.refresh.start", {})
    subset = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [first_id]},
    )

    assert second["job_id"] == first["job_id"]
    assert second["already_running"] is True
    assert subset["job_id"] == first["job_id"]
    assert subset["already_running"] is True

    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = controller.dispatch(
            "stores.refresh.status",
            {"job_id": first["job_id"]},
        )
        if status["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("store refresh job did not finish")

    assert status["status"] == "completed"
    assert status["worker_count"] == 2
    assert status["elapsed_seconds"] >= 0
    assert sorted(service.refreshed) == [first_id, second_id]
    assert sorted(service.scanned) == [first_id, second_id]


def test_overlapping_selection_skips_stores_already_being_refreshed(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-refresh-partial-dedupe.db")
    first_id = database.add_store("重叠店铺一", "wx-overlap-one", "secret")
    second_id = database.add_store("重叠店铺二", "wx-overlap-two", "secret")
    entered = threading.Event()
    release = threading.Event()

    class Service:
        def __init__(self) -> None:
            self.refreshed: list[int] = []

        def refresh_store_metrics(self, store):
            self.refreshed.append(store.id)
            if len(self.refreshed) == 1:
                entered.set()
                assert release.wait(3)
            return {}

    service = Service()
    controller = NextUiController(database, product_service=service)

    first = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [first_id]},
    )
    assert entered.wait(2)
    second = controller.dispatch("stores.refresh.start", {})
    release.set()

    assert second["job_id"] != first["job_id"]
    assert second["store_ids"] == [first_id, second_id]
    assert first["job_id"] in second["monitored_job_ids"]
    assert len(second["monitored_job_ids"]) == 2
    new_job_id = next(
        job_id
        for job_id in second["monitored_job_ids"]
        if job_id != first["job_id"]
    )
    new_job = controller.dispatch(
        "stores.refresh.status",
        {"job_id": new_job_id},
    )
    assert list(new_job["store_ids"]) == [second_id]
    controller.wait_for_store_refresh(first["job_id"], timeout=5)
    controller.wait_for_store_refresh(new_job_id, timeout=5)
    controller.wait_for_store_refresh(second["job_id"], timeout=5)
    assert sorted(service.refreshed) == [first_id, second_id]


def test_old_account_generation_refresh_is_never_reused(tmp_path: Path) -> None:
    database = Database(tmp_path / "store-refresh-generation.db")
    store_id = database.add_store("账号切换店铺", "wx-generation", "secret")

    class Service:
        def refresh_store_metrics(self, _store):
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = NextUiController(
        database,
        product_service=Service(),
        account_label="new-account",
    )
    current_generation = controller._current_generation()
    controller._store_jobs["old-job"] = {
        "job_id": "old-job",
        "account_id": "old-account",
        "generation": current_generation - 1,
        "store_ids": (store_id,),
        "store_id": store_id,
        "status": "running",
    }

    started = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [store_id]},
    )

    assert started["job_id"] != "old-job"
    assert started["already_running"] is False
    assert started["store_ids"] == [store_id]
    controller.wait_for_store_refresh(started["job_id"], timeout=5)


def test_finished_refresh_does_not_suppress_the_next_real_refresh(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-refresh-next-round.db")
    store_id = database.add_store("下一轮刷新店铺", "wx-next-round", "secret")
    completed = threading.Event()

    class Service:
        def __init__(self) -> None:
            self.refreshed: list[int] = []

        def refresh_store_metrics(self, store):
            self.refreshed.append(store.id)
            completed.set()
            return {}

        def scan_product_catalog(self, _store):
            return None

    service = Service()
    controller = NextUiController(database, product_service=service)

    first = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [store_id]},
    )
    assert completed.wait(2)
    controller.wait_for_store_refresh(first["job_id"], timeout=5)
    second = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [store_id]},
    )
    controller.wait_for_store_refresh(second["job_id"], timeout=5)

    assert second["job_id"] != first["job_id"]
    assert second["already_running"] is False
    assert service.refreshed == [store_id, store_id]


def test_store_refresh_status_rejects_another_account_job(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-refresh-owner.db")
    controller = NextUiController(database, account_label="current-account")
    controller._store_jobs["foreign-job"] = {
        "job_id": "foreign-job",
        "account_id": "other-account",
        "generation": controller._current_generation(),
        "status": "running",
    }

    with pytest.raises(PermissionError, match="不属于当前账户"):
        controller.dispatch(
            "stores.refresh.status",
            {"job_id": "foreign-job"},
        )


def test_store_refresh_status_rejects_previous_account_generation(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-refresh-generation-status.db")
    controller = NextUiController(database, account_label="current-account")
    controller._store_jobs["old-generation-job"] = {
        "job_id": "old-generation-job",
        "account_id": "current-account",
        "generation": controller._current_generation() - 1,
        "status": "running",
    }

    with pytest.raises(PermissionError, match="会话代际不匹配"):
        controller.dispatch(
            "stores.refresh.status",
            {"job_id": "old-generation-job"},
        )


def test_union_of_two_active_store_jobs_is_monitored_as_one_job(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-refresh-union.db")
    first_id = database.add_store("联合刷新一", "wx-union-one", "secret")
    second_id = database.add_store("联合刷新二", "wx-union-two", "secret")
    entered = {
        first_id: threading.Event(),
        second_id: threading.Event(),
    }
    release = {
        first_id: threading.Event(),
        second_id: threading.Event(),
    }

    class Service:
        def __init__(self) -> None:
            self.refreshed: list[int] = []

        def refresh_store_metrics(self, store):
            self.refreshed.append(store.id)
            entered[store.id].set()
            assert release[store.id].wait(3)
            return {}

        def scan_product_catalog(self, _store):
            return None

    service = Service()
    controller = NextUiController(database, product_service=service)
    first = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [first_id]},
    )
    assert entered[first_id].wait(2)
    second = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [second_id]},
    )
    assert entered[second_id].wait(2)

    combined = controller.dispatch("stores.refresh.start", {})

    assert combined["job_id"] not in {first["job_id"], second["job_id"]}
    assert combined["already_running"] is True
    assert combined["store_ids"] == [first_id, second_id]
    assert set(combined["monitored_job_ids"]) == {
        first["job_id"],
        second["job_id"],
    }
    release[first_id].set()
    assert controller.wait_for_store_refresh(first["job_id"], timeout=5)
    partial = controller.dispatch(
        "stores.refresh.status",
        {"job_id": combined["job_id"]},
    )
    assert partial["status"] not in {
        "completed",
        "partial",
        "failed",
        "cancelled",
    }

    release[second_id].set()
    assert controller.wait_for_store_refresh(second["job_id"], timeout=5)
    assert controller.wait_for_store_refresh(
        combined["job_id"],
        timeout=5,
    )
    final = controller.dispatch(
        "stores.refresh.status",
        {"job_id": combined["job_id"]},
    )

    assert final["status"] == "completed"
    assert final["current"] == 2
    assert final["total"] == 2
    assert final["succeeded"] == 2
    assert sorted(final["successful_store_ids"]) == [first_id, second_id]
    assert sorted(service.refreshed) == [first_id, second_id]


def test_partial_overlap_monitors_existing_and_new_refresh_jobs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "store-refresh-partial-monitor.db")
    first_id = database.add_store("部分监控一", "wx-monitor-one", "secret")
    second_id = database.add_store("部分监控二", "wx-monitor-two", "secret")
    entered = {
        first_id: threading.Event(),
        second_id: threading.Event(),
    }
    release = {
        first_id: threading.Event(),
        second_id: threading.Event(),
    }

    class Service:
        def __init__(self) -> None:
            self.refreshed: list[int] = []

        def refresh_store_metrics(self, store):
            self.refreshed.append(store.id)
            entered[store.id].set()
            assert release[store.id].wait(3)
            return {}

        def scan_product_catalog(self, _store):
            return None

    service = Service()
    controller = NextUiController(database, product_service=service)
    existing = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [first_id]},
    )
    assert entered[first_id].wait(2)

    combined = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [first_id, second_id]},
    )
    assert entered[second_id].wait(2)

    assert combined["job_id"] != existing["job_id"]
    assert existing["job_id"] in combined["monitored_job_ids"]
    assert len(combined["monitored_job_ids"]) == 2
    assert sorted(service.refreshed) == [first_id, second_id]

    release[first_id].set()
    assert controller.wait_for_store_refresh(existing["job_id"], timeout=5)
    release[second_id].set()
    assert controller.wait_for_store_refresh(
        combined["job_id"],
        timeout=5,
    )
    final = controller.dispatch(
        "stores.refresh.status",
        {"job_id": combined["job_id"]},
    )
    assert final["status"] == "completed"
    assert final["succeeded"] == 2
    assert sorted(service.refreshed) == [first_id, second_id]


@pytest.mark.parametrize(
    ("child_statuses", "expected_status"),
    [
        (("completed", "failed"), "partial"),
        (("completed", "cancelled"), "partial"),
        (("cancelled", "cancelled"), "cancelled"),
    ],
)
def test_aggregate_refresh_does_not_hide_failed_or_cancelled_children(
    tmp_path: Path,
    child_statuses: tuple[str, str],
    expected_status: str,
) -> None:
    database = Database(tmp_path / f"store-monitor-{child_statuses[0]}.db")
    first_id = database.add_store("聚合监控一", "wx-aggregate-one", "secret")
    second_id = database.add_store("聚合监控二", "wx-aggregate-two", "secret")
    class Service:
        pass

    controller = NextUiController(database, product_service=Service())
    owner = controller._job_owner_fields()
    child_jobs = {
        "child-one": {
            "job_id": "child-one",
            **owner,
            "store_ids": (first_id,),
            "status": "running",
            "current": 0,
            "total": 1,
            "succeeded": 0,
            "failed": 0,
            "successful_store_ids": [],
            "failed_store_ids": [],
            "errors": [],
        },
        "child-two": {
            "job_id": "child-two",
            **owner,
            "store_ids": (second_id,),
            "status": "running",
            "current": 0,
            "total": 1,
            "succeeded": 0,
            "failed": 0,
            "successful_store_ids": [],
            "failed_store_ids": [],
            "errors": [],
        },
    }
    with controller._store_jobs_lock:
        controller._store_jobs.update(child_jobs)

    combined = controller.dispatch("stores.refresh.start", {})
    with controller._store_jobs_lock:
        first_child = controller._store_jobs["child-one"]
        second_child = controller._store_jobs["child-two"]
        if child_statuses[0] == "completed":
            first_child.update(
                status="completed",
                current=1,
                succeeded=1,
                successful_store_ids=[first_id],
            )
        else:
            first_child.update(status="cancelled", current=1)
        if child_statuses[1] == "failed":
            second_child.update(
                status="failed",
                current=1,
                failed=1,
                failed_store_ids=[second_id],
                errors=["店铺二刷新失败"],
            )
        else:
            second_child.update(status="cancelled", current=1)

    assert controller.wait_for_store_refresh(
        combined["job_id"],
        timeout=5,
    )
    final = controller.dispatch(
        "stores.refresh.status",
        {"job_id": combined["job_id"]},
    )

    assert final["status"] == expected_status
    if child_statuses[1] == "failed":
        assert final["failed"] == 1
        assert "店铺二刷新失败" in final["errors"]
    else:
        assert final["failed"] == 0
        assert final["cancelled"] == (
            1 if child_statuses[0] == "completed" else 2
        )
