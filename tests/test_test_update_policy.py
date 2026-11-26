from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from weimi_manager import startup_update
from weimi_manager.startup_update import run_startup_update
from weimi_manager.update_policy import (
    TEST_POLICY_SCOPE,
    load_test_update_policy,
)
from weimi_manager.updater import StagedUpdate, UpdateInfo


def write_policy(target: Path, *, enabled: bool = True) -> Path:
    path = target / "_internal" / "test-update-policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "scope": TEST_POLICY_SCOPE,
                "enabled": enabled,
                "channel": "stable",
                "allow_prerelease": False,
                "install_path": str(target),
            }
        ),
        encoding="utf-8",
    )
    return path


def write_installed_version(target: Path, version: str) -> None:
    path = target / "_internal" / "update_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": version, "sha256": ""}),
        encoding="utf-8",
    )


def stable_update(current: str = "1.8.80") -> UpdateInfo:
    return UpdateInfo(
        current_version=current,
        latest_version="1.8.82",
        available=True,
        mandatory=False,
        channel="stable",
        prerelease=False,
    )


def test_policy_requires_exact_test_path_and_explicit_stable_config(
    tmp_path: Path,
) -> None:
    target = tmp_path / "LangLang"
    target.mkdir()
    write_policy(target)

    assert load_test_update_policy(
        target,
        allowed_install_dir=target,
    )
    assert (
        load_test_update_policy(
            target,
            allowed_install_dir=tmp_path / "OtherLangLang",
        )
        is None
    )


def test_enabled_test_policy_auto_stages_and_installs(
    tmp_path: Path,
) -> None:
    target = tmp_path / "LangLang"
    target.mkdir()
    write_policy(target)
    write_installed_version(target, "1.8.80")
    calls: list[str] = []
    staged = StagedUpdate(
        version="1.8.82",
        archive_path=tmp_path / "package.zip",
        staging_root=tmp_path / "stage",
        package_root=tmp_path / "stage" / "LangLang",
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        should_exit = run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=stable_update,
            stage=lambda _info, _target: calls.append("stage") or staged,
            launch=lambda _staged, _target: (
                calls.append("launch")
                or tmp_path / "update.log"
            ),
            notice=lambda _title, _message: None,
        )
        state = startup_update._read_auto_state(
            startup_update._auto_state_path(target)
        )

    assert should_exit is True
    assert calls == ["stage", "launch"]
    assert state["status"] == "pending_install"
    assert state["target_version"] == "1.8.82"


def test_auto_update_state_uses_injected_clock(tmp_path: Path) -> None:
    target = tmp_path / "LangLang"
    target.mkdir()
    write_policy(target)
    write_installed_version(target, "1.8.80")
    fixed = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)
    staged = StagedUpdate(
        version="1.8.82",
        archive_path=tmp_path / "package.zip",
        staging_root=tmp_path / "stage",
        package_root=tmp_path / "stage" / "LangLang",
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=stable_update,
            stage=lambda *_args: staged,
            launch=lambda *_args: tmp_path / "update.log",
            now_provider=lambda: fixed,
        )
        state = startup_update._read_auto_state(
            startup_update._auto_state_path(target)
        )

    assert state["updated_at"] == "2026-11-23T06:00:00+00:00"


def test_staging_validation_failure_persists_failed_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "LangLang"
    target.mkdir()
    write_policy(target)
    write_installed_version(target, "1.8.80")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        should_exit = run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=stable_update,
            stage=lambda *_args: (_ for _ in ()).throw(
                ValueError("更新包 SHA256 校验失败")
            ),
            launch=lambda *_args: pytest.fail(
                "failed validation must not launch"
            ),
        )
        state = startup_update._read_auto_state(
            startup_update._auto_state_path(target)
        )

    assert should_exit is False
    assert state["status"] == "failed"
    assert "SHA256" in state["last_error"]


@pytest.mark.parametrize(
    ("version", "channel", "prerelease"),
    [
        ("1.8.82", "beta", False),
        ("1.8.82", "stable", True),
        ("1.8.81", "stable", False),
        ("1.8.82", "", False),
    ],
)
def test_test_policy_rejects_non_stable_or_non_newer_release(
    tmp_path: Path,
    version: str,
    channel: str,
    prerelease: bool,
) -> None:
    target = tmp_path / "LangLang"
    target.mkdir()
    write_policy(target)
    write_installed_version(target, "1.8.82")
    stage_called = False

    def stage(_info, _target):
        nonlocal stage_called
        stage_called = True
        raise AssertionError("ineligible release must not download")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        should_exit = run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=lambda: UpdateInfo(
                current_version="1.8.82",
                latest_version=version,
                available=True,
                channel=channel,
                prerelease=prerelease,
            ),
            stage=stage,
            launch=lambda *_args: pytest.fail(
                "ineligible release must not install"
            ),
        )

    assert should_exit is False
    assert stage_called is False


def test_critical_tasks_defer_until_next_startup(tmp_path: Path) -> None:
    target = tmp_path / "LangLang"
    target.mkdir()
    write_policy(target)
    write_installed_version(target, "1.8.80")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        should_exit = run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=stable_update,
            critical_tasks=lambda: True,
            stage=lambda *_args: pytest.fail(
                "active tasks must defer before download"
            ),
            launch=lambda *_args: pytest.fail(
                "active tasks must defer before install"
            ),
        )
        state = startup_update._read_auto_state(
            startup_update._auto_state_path(target)
        )

    assert should_exit is False
    assert state["status"] == "pending_install"
    assert state["next_startup"] is True


def test_verified_package_is_reused_without_duplicate_download(
    tmp_path: Path,
) -> None:
    target = tmp_path / "LangLang"
    package_root = tmp_path / "stage" / "LangLang"
    package_root.mkdir(parents=True)
    (package_root / "LangLang.exe").write_bytes(b"exe")
    write_policy(target)
    write_installed_version(target, "1.8.80")
    launches: list[str] = []

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        state_path = startup_update._auto_state_path(target)
        startup_update._update_auto_state(
            state_path,
            "verified",
            install_path=str(target),
            current_version="1.8.80",
            target_version="1.8.82",
            archive_path=str(tmp_path / "package.zip"),
            staging_root=str(tmp_path / "stage"),
            package_root=str(package_root),
        )
        should_exit = run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=stable_update,
            stage=lambda *_args: pytest.fail(
                "verified package must be reused"
            ),
            launch=lambda _staged, _target: (
                launches.append("launch")
                or tmp_path / "update.log"
            ),
        )

    assert should_exit is True
    assert launches == ["launch"]


def test_next_startup_records_installed_and_rollback_statuses(
    tmp_path: Path,
) -> None:
    target = tmp_path / "LangLang"
    target.mkdir()
    write_policy(target)
    write_installed_version(target, "1.8.82")
    notices: list[str] = []

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        state_path = startup_update._auto_state_path(target)
        startup_update._update_auto_state(
            state_path,
            "pending_install",
            target_version="1.8.82",
            current_version="1.8.80",
        )
        run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=lambda: UpdateInfo(
                current_version="1.8.82",
                latest_version="1.8.82",
                available=False,
            ),
            notice=lambda _title, message: notices.append(message),
        )
        installed = startup_update._read_auto_state(state_path)

        write_installed_version(target, "1.8.80")
        update_log = tmp_path / "update.log"
        update_log.write_text(
            "Update failed: boom\nPrevious version restored\n",
            encoding="utf-8",
        )
        startup_update._update_auto_state(
            state_path,
            "pending_install",
            target_version="1.8.82",
            current_version="1.8.80",
            installer_log=str(update_log),
        )
        run_startup_update(
            project_dir=target,
            force=True,
            allowed_test_install_dir=target,
            check=lambda: UpdateInfo(
                current_version="1.8.80",
                latest_version="1.8.80",
                available=False,
            ),
            notice=lambda _title, message: notices.append(message),
        )
        rolled_back = startup_update._read_auto_state(state_path)

    assert installed["status"] == "installed"
    assert rolled_back["status"] == "rolled_back"
    assert any("已更新到1.8.82" in message for message in notices)
    assert any("已恢复原版本" in message for message in notices)
