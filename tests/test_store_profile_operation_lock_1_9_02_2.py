from __future__ import annotations

from pathlib import Path
import threading

import pytest

from weimi_manager.db import Database
from weimi_manager.webui.controller import NextUiController


class BlockingWebSession:
    def __init__(self) -> None:
        self.login_started = threading.Event()
        self.login_release = threading.Event()
        self.open_started = threading.Event()
        self.open_release = threading.Event()
        self.logged_in: list[int] = []
        self.opened: list[int] = []
        self.blocked_login_store_id = 0
        self.blocked_open_store_id = 0
        self.fail_login_store_id = 0
        self.fail_open_store_id = 0

    def interactive_login(self, store, *, allowed_store_ids=None, **_kwargs):
        self.logged_in.append(store.id)
        if store.id == self.fail_login_store_id:
            raise RuntimeError("模拟登录浏览器异常")
        if store.id == self.blocked_login_store_id:
            self.login_started.set()
            if not self.login_release.wait(timeout=3):
                raise TimeoutError("测试登录未释放")
        return {
            "success": True,
            "matched_store_ids": sorted(allowed_store_ids or {store.id}),
        }

    def open_official_backend(self, store):
        if store.id == self.fail_open_store_id:
            raise RuntimeError("模拟后台窗口异常")
        if store.id == self.blocked_open_store_id:
            self.open_started.set()
            if not self.open_release.wait(timeout=3):
                raise TimeoutError("测试后台窗口未释放")
        self.opened.append(store.id)


def _run_dispatch(
    controller: NextUiController,
    action: str,
    payload: dict,
    results: dict[str, dict],
    key: str,
) -> threading.Thread:
    def dispatch() -> None:
        results[key] = controller.dispatch(action, payload)

    worker = threading.Thread(target=dispatch)
    worker.start()
    return worker


def test_same_store_profile_operation_is_mutually_exclusive(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("同店", "wx-profile-same", "secret")
    web_session = BlockingWebSession()
    web_session.blocked_login_store_id = store_id
    controller = NextUiController(database, web_session=web_session)
    results: dict[str, dict] = {}

    login_worker = _run_dispatch(
        controller,
        "stores.login",
        {"store_id": store_id},
        results,
        "login",
    )
    assert web_session.login_started.wait(timeout=2)
    try:
        blocked = controller.dispatch(
            "stores.open-backend",
            {"store_id": store_id},
        )
        assert blocked["busy"] is True
        assert blocked["store_id"] == store_id
        assert "该店浏览器操作正在进行" in blocked["message"]
        assert web_session.opened == []
    finally:
        web_session.login_release.set()
        login_worker.join(timeout=3)

    assert not login_worker.is_alive()
    assert results["login"]["saved_store_count"] == 1
    opened = controller.dispatch(
        "stores.open-backend",
        {"store_id": store_id},
    )
    assert opened == {"opened": store_id}
    assert web_session.opened == [store_id]


def test_profile_lock_is_released_after_backend_exception(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("后台异常", "wx-profile-open-error", "secret")
    web_session = BlockingWebSession()
    web_session.fail_open_store_id = store_id
    controller = NextUiController(database, web_session=web_session)

    with pytest.raises(RuntimeError, match="模拟后台窗口异常"):
        controller.dispatch("stores.open-backend", {"store_id": store_id})

    login = controller.dispatch("stores.login", {"store_id": store_id})
    assert login["saved_store_count"] == 1
    assert web_session.opened == []


def test_open_backend_blocks_same_store_login_until_window_closes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("后台同店", "wx-profile-open", "secret")
    web_session = BlockingWebSession()
    web_session.blocked_open_store_id = store_id
    controller = NextUiController(database, web_session=web_session)
    results: dict[str, dict] = {}

    open_worker = _run_dispatch(
        controller,
        "stores.open-backend",
        {"store_id": store_id},
        results,
        "open",
    )
    assert web_session.open_started.wait(timeout=2)
    try:
        blocked = controller.dispatch(
            "stores.login",
            {"store_id": store_id},
        )
        assert blocked["busy"] is True
        assert blocked["requested_store_ids"] == [store_id]
        assert "该店浏览器操作正在进行" in blocked["message"]
        assert web_session.logged_in == []
    finally:
        web_session.open_release.set()
        open_worker.join(timeout=3)

    assert not open_worker.is_alive()
    assert results["open"] == {"opened": store_id}
    login = controller.dispatch("stores.login", {"store_id": store_id})
    assert login["saved_store_count"] == 1


def test_login_batch_locks_only_selected_store_profiles(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first_id = database.add_store("批量一", "wx-profile-batch-1", "secret")
    second_id = database.add_store("批量二", "wx-profile-batch-2", "secret")
    other_id = database.add_store("异店", "wx-profile-other", "secret")
    web_session = BlockingWebSession()
    web_session.blocked_login_store_id = first_id
    controller = NextUiController(database, web_session=web_session)
    results: dict[str, dict] = {}

    login_worker = _run_dispatch(
        controller,
        "stores.login",
        {"store_ids": [first_id, second_id]},
        results,
        "batch",
    )
    assert web_session.login_started.wait(timeout=2)
    try:
        selected_blocked = controller.dispatch(
            "stores.open-backend",
            {"store_id": second_id},
        )
        assert selected_blocked["busy"] is True
        assert selected_blocked["store_id"] == second_id

        other_opened = controller.dispatch(
            "stores.open-backend",
            {"store_id": other_id},
        )
        assert other_opened == {"opened": other_id}
        assert web_session.opened == [other_id]
    finally:
        web_session.login_release.set()
        login_worker.join(timeout=3)

    assert not login_worker.is_alive()
    assert results["batch"]["saved_store_count"] == 2


def test_disjoint_store_logins_run_in_parallel(tmp_path: Path) -> None:
    database = Database(tmp_path / "webui.db")
    first_id = database.add_store("并行一", "wx-profile-parallel-1", "secret")
    second_id = database.add_store("并行二", "wx-profile-parallel-2", "secret")
    web_session = BlockingWebSession()
    web_session.blocked_login_store_id = first_id
    controller = NextUiController(database, web_session=web_session)
    results: dict[str, dict] = {}

    first_worker = _run_dispatch(
        controller,
        "stores.login",
        {"store_id": first_id},
        results,
        "first",
    )
    assert web_session.login_started.wait(timeout=2)
    try:
        second_worker = _run_dispatch(
            controller,
            "stores.login",
            {"store_id": second_id},
            results,
            "second",
        )
        second_worker.join(timeout=2)
        assert not second_worker.is_alive()
        assert results["second"]["saved_store_count"] == 1
    finally:
        web_session.login_release.set()
        first_worker.join(timeout=3)

    assert not first_worker.is_alive()
    assert results["first"]["saved_store_count"] == 1


def test_profile_lock_is_released_after_login_exception(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    store_id = database.add_store("异常释放", "wx-profile-error", "secret")
    web_session = BlockingWebSession()
    web_session.fail_login_store_id = store_id
    controller = NextUiController(database, web_session=web_session)

    with pytest.raises(RuntimeError, match="模拟登录浏览器异常"):
        controller.dispatch("stores.login", {"store_id": store_id})

    opened = controller.dispatch(
        "stores.open-backend",
        {"store_id": store_id},
    )
    assert opened == {"opened": store_id}
    assert web_session.opened == [store_id]
