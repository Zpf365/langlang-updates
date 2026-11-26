from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager.db import Database
from weimi_manager.runtime_logging import configure_runtime_logging
from weimi_manager.updater import StagedUpdate, UpdateInfo
from weimi_manager.webui.controller import NextUiController


def _internal_context() -> SimpleNamespace:
    return SimpleNamespace(
        account_id="220a9616-2346-441b-9e3f-f5f4f5b824fd",
        username="zpfly",
        offline=False,
        member_role="owner",
        permissions={},
        tokens=SimpleNamespace(access_token="test-token"),
    )


def test_controller_dispatch_logs_success_failure_and_redaction(
    tmp_path: Path,
) -> None:
    project = tmp_path / "app"
    manager = configure_runtime_logging(
        project,
        local_app_data=tmp_path / "local",
        force=True,
    )
    database = Database(project / "data" / "webui.db")
    controller = NextUiController(
        database,
        project_dir=project,
        auth_context=_internal_context(),
    )

    controller.dispatch(
        "stores.list",
        {
            "store_id": 18,
            "password": "never-log-this",
            "cookie": "session=never-log-this",
        },
        origin="auto_refresh",
    )
    try:
        controller.dispatch("stores.delete", {})
    except ValueError:
        pass
    else:
        raise AssertionError("missing store_id must fail")

    lines = [
        json.loads(line)
        for line in (manager.day_dir / "network.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    success = next(row for row in lines if row["action"] == "stores.list")
    failure = next(row for row in lines if row["action"] == "stores.delete")
    assert success["status"] == "success"
    assert success["origin"] == "auto_refresh"
    assert success["store_id"] == "18"
    assert success["parameters"]["password"] == "[REDACTED]"
    assert success["parameters"]["cookie"] == "[REDACTED]"
    assert failure["status"] == "failed"
    assert failure["error_code"]
    assert failure["duration_ms"] >= 0
    manager.close()


def test_webui_event_batch_and_log_export_rpcs(tmp_path: Path) -> None:
    project = tmp_path / "app"
    manager = configure_runtime_logging(
        project,
        local_app_data=tmp_path / "local",
        force=True,
    )
    controller = NextUiController(
        Database(project / "data" / "webui.db"),
        project_dir=project,
        auth_context=_internal_context(),
    )
    accepted = controller.dispatch(
        "logs.webui-events",
        {
            "events": [
                {
                    "kind": "click",
                    "route": "/shops",
                    "control_text": "刷新",
                    "disabled": False,
                }
            ]
        },
    )
    assert accepted == {"accepted": 1}
    event = json.loads(
        (manager.day_dir / "webui-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert event["route"] == "/shops"
    assert event["control_text"] == "刷新"

    exported = controller.dispatch("logs.export", {"days": 1})
    assert Path(exported["path"]).is_file()
    assert exported["days"] == 1
    manager.close()


def test_webui_update_job_reports_real_progress_and_restart(
    tmp_path: Path,
) -> None:
    project = tmp_path / "app"
    project.mkdir(parents=True)
    manager = configure_runtime_logging(
        project,
        local_app_data=tmp_path / "local",
        force=True,
    )
    controller = NextUiController(
        Database(project / "data" / "webui.db"),
        project_dir=project,
        auth_context=_internal_context(),
    )
    info = UpdateInfo(
        current_version="1.8.80",
        latest_version="1.8.81",
        available=True,
        download_url="https://example.invalid/update.zip",
        notes="测试",
    )
    staged = StagedUpdate(
        version="1.8.81",
        archive_path=tmp_path / "update.zip",
        staging_root=tmp_path / "stage",
        package_root=tmp_path / "stage" / "LangLang",
    )
    restarted = threading.Event()
    controller.account_action_handler = (
        lambda action: restarted.set()
        if action == "update"
        else None
    )
    with (
        mock.patch(
            "weimi_manager.webui.controller.check_for_update",
            return_value=info,
        ),
        mock.patch(
            "weimi_manager.webui.controller.download_and_stage_update",
            side_effect=lambda *_args, progress=None, **_kwargs: (
                progress("正在下载更新包", 50, 100),
                progress("正在校验更新包 SHA256", 100, 100),
                progress("正在解压更新包", 100, 100),
                staged,
            )[-1],
        ),
        mock.patch(
            "weimi_manager.webui.controller.launch_update_installer",
            return_value=tmp_path / "update.log",
        ),
    ):
        checked = controller.dispatch("update.check", {"manual": True})
        assert checked["available"] is True
        started = controller.dispatch(
            "update.install.start",
            {"latest_version": "1.8.81"},
        )
        deadline = time.time() + 5
        status = {}
        while time.time() < deadline:
            status = controller.dispatch(
                "update.install.status",
                {"job_id": started["job_id"]},
            )
            if status["status"] == "installing":
                break
            time.sleep(0.03)
        assert status["status"] == "installing"
        assert status["message"] == "更新包已通过校验，正在自动安装并重启"
        assert restarted.wait(3)
    assert "update.webui.installer_started" in (
        manager.day_dir / "app.log"
    ).read_text(encoding="utf-8")
    manager.close()
