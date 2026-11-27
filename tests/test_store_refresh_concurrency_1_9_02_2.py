from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest import mock

from weimi_manager.db import Database
from weimi_manager.store_refresh_pool import resolve_store_refresh_workers
from weimi_manager.webui.controller import NextUiController


def _controller_with_stores(
    tmp_path: Path,
    count: int,
    service: object,
) -> NextUiController:
    database = Database(tmp_path / f"store-refresh-{count}.db")
    for index in range(count):
        database.add_store(
            f"并发店铺{index}",
            f"wx-concurrency-{index}",
            "secret",
        )
    return NextUiController(database, product_service=service)


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def test_store_refresh_budget_is_adaptive_with_a_six_to_twelve_window() -> None:
    assert resolve_store_refresh_workers(
        120,
        cpu_count=2,
        available_memory_mb=768,
    ) == 6
    assert 6 <= resolve_store_refresh_workers(
        120,
        cpu_count=8,
        available_memory_mb=8192,
    ) <= 12
    assert resolve_store_refresh_workers(
        120,
        configured=99,
        cpu_count=8,
        available_memory_mb=8192,
    ) == 12
    assert resolve_store_refresh_workers(
        4,
        cpu_count=8,
        available_memory_mb=8192,
    ) == 4


def test_hundred_store_refresh_keeps_the_six_to_twelve_bounds(
    tmp_path: Path,
) -> None:
    release = threading.Event()
    at_least_six = threading.Event()
    state_lock = threading.Lock()
    active = 0
    max_active = 0
    started: list[int] = []

    class Service:
        def refresh_store_metrics(self, store):
            nonlocal active, max_active
            with state_lock:
                started.append(store.id)
                active += 1
                max_active = max(max_active, active)
                if active >= 6:
                    at_least_six.set()
            assert release.wait(5)
            with state_lock:
                active -= 1
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = _controller_with_stores(tmp_path, 105, Service())
    with mock.patch(
        "weimi_manager.store_refresh_pool.detect_store_refresh_budget",
        return_value=12,
    ):
        started_job = controller.dispatch("stores.refresh.start", {})
        assert at_least_six.wait(3)
        status = controller.dispatch(
            "stores.refresh.status",
            {"job_id": started_job["job_id"]},
        )

        assert 6 <= status["active"] <= 12
        assert status["max"] == 12
        assert status["max_workers"] == 12
        assert status["queued"] == 105 - status["active"]

        release.set()
        assert controller.wait_for_store_refresh(
            started_job["job_id"],
            timeout=10,
        )

    final = controller.dispatch(
        "stores.refresh.status",
        {"job_id": started_job["job_id"]},
    )
    assert final["status"] == "completed"
    assert final["completed"] == 105
    assert final["failed"] == 0
    assert final["cancelled"] == 0
    assert final["queued"] == 0
    assert final["active"] == 0
    assert 6 <= max_active <= 12
    assert len(started) == 105
    assert len(set(started)) == 105


def test_one_failed_store_does_not_stop_the_remaining_store_tasks(
    tmp_path: Path,
) -> None:
    failed_id = 1

    class Service:
        def refresh_store_metrics(self, store):
            if store.id == failed_id:
                raise RuntimeError("单店刷新失败")
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = _controller_with_stores(tmp_path, 8, Service())
    with mock.patch(
        "weimi_manager.store_refresh_pool.detect_store_refresh_budget",
        return_value=6,
    ):
        started = controller.dispatch("stores.refresh.start", {})
        assert controller.wait_for_store_refresh(
            started["job_id"],
            timeout=5,
        )

    status = controller.dispatch(
        "stores.refresh.status",
        {"job_id": started["job_id"]},
    )
    assert status["status"] == "partial"
    assert status["completed"] == 8
    assert status["failed"] == 1
    assert status["succeeded"] == 7
    assert status["failed_store_ids"] == [failed_id]


def test_cancel_stops_dispatching_new_stores_and_waits_for_active_tasks(
    tmp_path: Path,
) -> None:
    release = threading.Event()
    at_limit = threading.Event()
    state_lock = threading.Lock()
    started: list[int] = []
    active = 0

    class Service:
        def refresh_store_metrics(self, store):
            nonlocal active
            with state_lock:
                started.append(store.id)
                active += 1
                if active == 6:
                    at_limit.set()
            assert release.wait(5)
            with state_lock:
                active -= 1
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = _controller_with_stores(tmp_path, 20, Service())
    with mock.patch(
        "weimi_manager.store_refresh_pool.detect_store_refresh_budget",
        return_value=6,
    ):
        started_job = controller.dispatch("stores.refresh.start", {})
        assert at_limit.wait(3)
        cancelled = controller.dispatch(
            "stores.refresh.cancel",
            {
                "job_id": started_job["job_id"],
                "reason": "page_leave",
            },
        )
        assert cancelled["cancel_requested"] is True
        time.sleep(0.15)
        with state_lock:
            assert len(started) == 6

        release.set()
        assert controller.wait_for_store_refresh(
            started_job["job_id"],
            timeout=5,
        )

    final = controller.dispatch(
        "stores.refresh.status",
        {"job_id": started_job["job_id"]},
    )
    assert final["status"] == "cancelled"
    assert final["completed"] == 6
    assert final["cancelled"] == 14
    assert final["failed"] == 0
    assert final["active"] == 0
    assert final["queued"] == 0
    assert len(started) == 6


def test_late_result_from_old_generation_does_not_update_the_job(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class Service:
        def refresh_store_metrics(self, _store):
            entered.set()
            assert release.wait(5)
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = _controller_with_stores(tmp_path, 1, Service())
    started = controller.dispatch("stores.refresh.start", {})
    assert entered.wait(2)
    old_generation = controller._account_generation
    controller._freeze_account_work("switch")
    release.set()
    assert controller.wait_for_store_refresh(
        started["job_id"],
        timeout=5,
    )

    with controller._store_jobs_lock:
        job = dict(controller._store_jobs[started["job_id"]])
    assert controller._job_update_allowed(old_generation) is False
    assert job["status"] == "paused"
    assert job["successful_store_ids"] == []
    assert job["succeeded"] == 0


def test_completed_refresh_history_is_kept_without_worker_or_lock_objects(
    tmp_path: Path,
) -> None:
    class Service:
        def refresh_store_metrics(self, _store):
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = _controller_with_stores(tmp_path, 1, Service())
    history: list[str] = []

    for _round in range(6):
        started = controller.dispatch("stores.refresh.start", {})
        deadline = time.monotonic() + 5
        while True:
            status = controller.dispatch(
                "stores.refresh.status",
                {"job_id": started["job_id"]},
            )
            if status["status"] in {
                "completed",
                "partial",
                "failed",
                "cancelled",
            }:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("refresh did not reach a terminal status")
            time.sleep(0.01)
        assert status["status"] == "completed"
        history.append(started["job_id"])

        with controller._store_jobs_lock:
            job = controller._store_jobs[started["job_id"]]
            assert not any(key.startswith("_") for key in job)
        assert started["job_id"] not in controller._store_refresh_workers

    assert history == list(dict.fromkeys(history))
    with controller._store_jobs_lock:
        assert all(job_id in controller._store_jobs for job_id in history)
        assert not controller._store_refresh_workers
        assert not any(
            key.startswith("_")
            for job_id in history
            for key in controller._store_jobs[job_id]
        )


def test_active_or_unreadable_generation_keeps_refresh_transients(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class Service:
        def refresh_store_metrics(self, _store):
            entered.set()
            assert release.wait(5)
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = _controller_with_stores(tmp_path, 1, Service())
    started = controller.dispatch("stores.refresh.start", {})
    assert entered.wait(2)

    running = controller.dispatch(
        "stores.refresh.status",
        {"job_id": started["job_id"]},
    )
    assert running["status"] == "running"
    assert started["job_id"] in controller._store_refresh_workers
    with controller._store_jobs_lock:
        assert "_cancel_event" in controller._store_jobs[started["job_id"]]
        assert "_dispatch_lock" in controller._store_jobs[started["job_id"]]

    controller._freeze_account_work("switch")
    release.set()
    assert controller.wait_for_store_refresh(
        started["job_id"],
        timeout=5,
    )
    with controller._store_jobs_lock:
        paused = controller._store_jobs[started["job_id"]]
        assert paused["status"] == "paused"
        assert "_cancel_event" in paused
        assert "_dispatch_lock" in paused
    assert started["job_id"] in controller._store_refresh_workers


def test_aggregate_monitor_history_survives_transient_cleanup(
    tmp_path: Path,
) -> None:
    entered = {
        1: threading.Event(),
        2: threading.Event(),
    }
    release = threading.Event()

    class Service:
        def refresh_store_metrics(self, store):
            entered[store.id].set()
            assert release.wait(5)
            return {}

        def scan_product_catalog(self, _store):
            return None

    controller = _controller_with_stores(tmp_path, 2, Service())
    first = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [1]},
    )
    assert entered[1].wait(2)
    second = controller.dispatch(
        "stores.refresh.start",
        {"store_ids": [2]},
    )
    assert entered[2].wait(2)
    combined = controller.dispatch("stores.refresh.start", {})

    release.set()
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
    job_ids = {
        first["job_id"],
        second["job_id"],
        combined["job_id"],
    }
    with controller._store_jobs_lock:
        assert job_ids.issubset(controller._store_jobs)
        assert not (job_ids & set(controller._store_refresh_workers))
        for job_id in job_ids:
            assert not any(
                key.startswith("_")
                for key in controller._store_jobs[job_id]
            )
