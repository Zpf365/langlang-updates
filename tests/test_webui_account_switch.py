from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from weimi_manager.auth_client import LicenseStatus, SessionTokens
from weimi_manager.auth_service import AuthContext, AuthService
from weimi_manager.db import Database
from weimi_manager.webui import main as webui_main
from weimi_manager.webui.controller import NextUiController
from weimi_manager.webui.server import FrontendServer


def auth_context(account_id: str, username: str) -> AuthContext:
    return AuthContext(
        tokens=SessionTokens(
            user_id=account_id,
            account_id=account_id,
            username=username,
            email=f"{username}@example.test",
            access_token=f"token-{account_id}",
            refresh_token=f"refresh-{account_id}",
            expires_at="2099-01-01T00:00:00+00:00",
        ),
        license=LicenseStatus(
            active=True,
            permanent=True,
            expires_at=None,
            status_text="永久授权",
            remaining_days=999999,
            max_devices=1,
            device_bound=True,
            server_time="2026-11-21T00:00:00+00:00",
        ),
        device_hash="device",
        device_name="test",
        organization_name="测试组织",
        member_role="owner",
    )


class AccountService:
    def __init__(self) -> None:
        self.logout_calls: list[tuple[str, bool]] = []

    def logout(self, context, *, forget_local: bool = False) -> None:
        self.logout_calls.append((context.account_id, forget_local))


def test_bootstrap_exposes_account_and_license_without_tokens(tmp_path: Path) -> None:
    database = Database(tmp_path / "account.db")
    context = auth_context("account-one", "账号一")
    controller = NextUiController(
        database,
        auth_context=context,
        auth_client=SimpleNamespace(organization_context=lambda _token: {
            "organization_id": "org",
            "role": "owner",
            "permissions": {},
        }),
    )

    payload = controller.dispatch("bootstrap", {})
    rendered = str(payload)

    assert payload["account"]["account_id"] == "account-one"
    assert payload["account"]["username"] == "账号一"
    assert payload["account"]["license"]["status_text"] == "永久授权"
    assert "token-account-one" not in rendered
    assert "refresh-account-one" not in rendered


def test_switch_and_logout_are_never_blocked_and_freeze_owned_jobs(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "account.db")
    service = AccountService()
    context = auth_context(
        "220a9616-2346-441b-9e3f-f5f4f5b824fd",
        "zpfly",
    )
    observed: list[str] = []
    controller = NextUiController(
        database,
        auth_context=context,
        auth_service=service,
    )
    controller.account_action_handler = observed.append
    scheduled = mock.Mock()
    scheduled.is_alive.return_value = True
    controller._upload_category_timer = scheduled
    controller._store_jobs["active"] = {
        "job_id": "active",
        "status": "running",
        "current": 4,
        "total": 189,
        "message": "刷新店铺 4/189",
    }

    status = controller.dispatch("account.task-status", {})
    assert status["active"] is True
    assert status["tasks"][0]["kind"] == "店铺刷新"
    assert status["tasks"][0]["account_id"] == (
        "220a9616-2346-441b-9e3f-f5f4f5b824fd"
    )
    old_generation = controller._account_generation

    switched = controller.dispatch("account.action", {"action": "switch"})
    assert switched["accepted"] is True
    assert switched["forget_local"] is False
    assert switched["paused_task_count"] == 1
    assert switched["active_tasks"][0]["job_id"] == "active"
    assert observed == ["switch"]
    assert service.logout_calls == []
    scheduled.cancel.assert_called_once_with()
    active_job = controller._store_jobs["active"]
    assert active_job["status"] == "paused"
    assert active_job["paused_for_account_action"] == "switch"
    assert active_job["resume_requires_account"] == (
        "220a9616-2346-441b-9e3f-f5f4f5b824fd"
    )
    assert controller._job_update_allowed(old_generation) is False
    assert controller.dispatch("account.task-status", {})["active"] is False
    assert controller.dispatch("account.task-status", {})["closing"] is True
    with pytest.raises(RuntimeError, match="冻结"):
        controller.dispatch("orders.list", {})

    logged_out = controller.dispatch("account.action", {"action": "logout"})
    assert logged_out["forget_local"] is True
    assert observed == ["switch", "logout"]
    assert service.logout_calls == [
        ("220a9616-2346-441b-9e3f-f5f4f5b824fd", True)
    ]


def test_logout_still_hands_off_when_remote_logout_fails(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "account.db")
    service = AccountService()
    service.logout = mock.Mock(side_effect=RuntimeError("network down"))
    context = auth_context("account-one", "zpfly")
    observed: list[str] = []
    controller = NextUiController(
        database,
        auth_context=context,
        auth_service=service,
    )
    controller.account_action_handler = observed.append
    controller._order_jobs["order-active"] = {
        "job_id": "order-active",
        "status": "running",
        "message": "同步订单中",
    }

    result = controller.dispatch("account.action", {"action": "logout"})

    assert result["accepted"] is True
    assert result["logout_error"] == "network down"
    assert result["paused_task_count"] == 1
    assert observed == ["logout"]


def test_account_freeze_covers_every_background_job_family(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "account-all-jobs.db")
    context = auth_context(
        "220a9616-2346-441b-9e3f-f5f4f5b824fd",
        "zpfly",
    )
    controller = NextUiController(
        database,
        auth_context=context,
        auth_service=AccountService(),
    )
    families = (
        ("store", controller._store_jobs),
        ("product", controller._product_jobs),
        ("order", controller._order_jobs),
        ("upload", controller._upload_jobs),
        ("automation", controller._automation_jobs),
        ("renewal", controller._activity_renewal_jobs),
        ("update", controller._update_jobs),
    )
    paused_events = {}
    for name, jobs in families:
        paused = mock.Mock()
        paused.set = mock.Mock()
        paused_events[name] = paused
        jobs[f"{name}-active"] = {
            "job_id": f"{name}-active",
            "status": "running",
            "message": f"{name} running",
            "account_id": context.account_id,
            "generation": controller._account_generation,
            "runner": SimpleNamespace(paused=paused),
        }
    old_generation = controller._account_generation

    result = controller.dispatch(
        "account.action",
        {"action": "switch"},
    )

    assert result["accepted"] is True
    assert result["paused_task_count"] == len(families)
    assert {
        task["kind"]
        for task in result["active_tasks"]
    } == {
        "店铺刷新",
        "商品刷新",
        "订单刷新",
        "上传任务",
        "自动化",
        "活动续期",
        "软件更新",
    }
    for name, jobs in families:
        job = jobs[f"{name}-active"]
        assert job["status"] == "paused"
        assert job["account_id"] == context.account_id
        assert job["resume_requires_account"] == context.account_id
        paused_events[name].set.assert_called_once_with()
    assert controller._job_update_allowed(old_generation) is False
    assert controller.dispatch("account.task-status", {})["active"] is False


def test_new_account_database_does_not_expose_old_account_jobs(
    tmp_path: Path,
) -> None:
    first = NextUiController(
        Database(tmp_path / "account-a.db"),
        auth_context=auth_context("account-a", "账号A"),
    )
    first._upload_jobs["upload-a"] = {
        "job_id": "upload-a",
        "status": "running",
        "account_id": "account-a",
    }
    second = NextUiController(
        Database(tmp_path / "account-b.db"),
        auth_context=auth_context("account-b", "账号B"),
    )

    assert second._active_account_tasks() == []
    assert second.dispatch("account.task-status", {})["active"] is False


@pytest.mark.parametrize("first_result", ["switch", "logout"])
def test_webui_outer_loop_reopens_login_and_uses_account_paths(
    tmp_path: Path,
    first_result: str,
) -> None:
    base = mock.Mock()
    base.data_dir = tmp_path / "data"
    base.project_dir = tmp_path
    base.resource_dir = tmp_path
    first_paths = SimpleNamespace(
        database=tmp_path / "first.db",
        data_dir=tmp_path / "first",
        resource_dir=tmp_path,
    )
    second_paths = SimpleNamespace(
        database=tmp_path / "second.db",
        data_dir=tmp_path / "second",
        resource_dir=tmp_path,
    )
    base.for_account.side_effect = [first_paths, second_paths]
    first_context = auth_context("first", "一号")
    second_context = auth_context("second", "二号")
    service = SimpleNamespace(client=object(), store=object())
    guard = mock.Mock()
    guard.acquire.return_value = True
    first_controller = mock.Mock()
    second_controller = mock.Mock()

    with (
        mock.patch.object(webui_main.AppPaths, "discover", return_value=base),
        mock.patch.object(webui_main, "SingleInstanceGuard", return_value=guard),
        mock.patch("weimi_manager.auth_config.AuthConfig.load"),
        mock.patch(
            "weimi_manager.auth_service.build_auth_service",
            return_value=service,
        ),
        mock.patch(
            "weimi_manager.ui.auth_window.AuthWindow",
            side_effect=[
                SimpleNamespace(mainloop=lambda: None, result=first_context),
                SimpleNamespace(mainloop=lambda: None, result=second_context),
            ],
        ) as auth_window,
        mock.patch.object(webui_main, "Database") as database,
        mock.patch(
            "weimi_manager.web_session.WechatWebSession"
        ),
        mock.patch("weimi_manager.service.SyncService"),
        mock.patch("weimi_manager.team_service.TeamService"),
        mock.patch.object(
            webui_main,
            "NextUiController",
            side_effect=[first_controller, second_controller],
        ) as controller,
        mock.patch.object(
            webui_main,
            "run_desktop",
            side_effect=[first_result, "closed"],
        ) as run_desktop,
    ):
        assert webui_main.main([]) == 0

    assert auth_window.call_count == 2
    assert base.for_account.call_args_list == [
        mock.call("first"),
        mock.call("second"),
    ]
    assert database.call_args_list == [
        mock.call(first_paths.database),
        mock.call(second_paths.database),
    ]
    assert controller.call_count == 2
    assert controller.call_args_list[0].kwargs["auth_service"] is service
    assert controller.call_args_list[1].kwargs["auth_context"] is second_context
    assert run_desktop.call_args_list == [
        mock.call(first_controller, resource_dir=tmp_path, title="澜琅 · 微信小店版 一号"),
        mock.call(second_controller, resource_dir=tmp_path, title="澜琅 · 微信小店版 二号"),
    ]
    guard.close.assert_called_once_with()


def test_each_webui_server_uses_a_new_session_token(tmp_path: Path) -> None:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    controller = mock.Mock()
    first = FrontendServer(static, controller)
    second = FrontendServer(static, controller)
    try:
        first.start()
        second.start()
        assert first.token != second.token
        assert first.entry_url != second.entry_url
    finally:
        first.stop()
        second.stop()


def test_auth_service_logout_forgets_local_only_when_requested() -> None:
    client = mock.Mock()
    store = mock.Mock()
    service = AuthService(client, store)
    context = auth_context("account", "账号")

    service.logout(context, forget_local=False)
    client.logout.assert_called_once_with("token-account")
    store.remove.assert_not_called()

    service.logout(context, forget_local=True)
    store.remove.assert_called_once_with("account")


def test_auth_service_logout_removes_local_record_even_if_remote_fails() -> None:
    client = mock.Mock()
    client.logout.side_effect = RuntimeError("network down")
    store = mock.Mock()
    service = AuthService(client, store)
    context = auth_context("account", "账号")

    with pytest.raises(RuntimeError, match="network down"):
        service.logout(context, forget_local=True)

    store.remove.assert_called_once_with("account")
