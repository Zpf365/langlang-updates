from __future__ import annotations

from pathlib import Path

from weimi_manager.startup_health import StartupHealthCoordinator
from weimi_manager.update_health import (
    begin_update_attempt,
    read_update_health,
)


def test_auth_window_requires_loop_and_stable_mapping_before_marker(
    tmp_path: Path,
) -> None:
    project = tmp_path / "LangLang"
    project.mkdir()
    attempt = begin_update_attempt(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )
    coordinator = StartupHealthCoordinator(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )

    coordinator.report_auth_loop_entered()
    assert (
        read_update_health(
            project,
            local_app_data=tmp_path / "local",
        )
        == {}
    )

    coordinator.report_auth_stable()
    payload = read_update_health(
        project,
        local_app_data=tmp_path / "local",
    )

    assert payload["attempt_id"] == attempt.attempt_id
    assert payload["version"] == "1.9.02"
    assert payload["startup_nonce"]


def test_webui_path_requires_server_load_and_ready_marker(
    tmp_path: Path,
) -> None:
    project = tmp_path / "LangLang"
    project.mkdir()
    begin_update_attempt(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )
    coordinator = StartupHealthCoordinator(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )
    coordinator.expect_webui()
    coordinator.report_auth_loop_entered()
    coordinator.report_auth_stable()
    assert (
        read_update_health(
            project,
            local_app_data=tmp_path / "local",
        )
        == {}
    )

    coordinator.report_server_started()
    coordinator.report_webui_loaded()
    assert (
        read_update_health(
            project,
            local_app_data=tmp_path / "local",
        )
        == {}
    )

    coordinator.report_webui_ready()
    payload = read_update_health(
        project,
        local_app_data=tmp_path / "local",
    )

    assert payload["version"] == "1.9.02"
    assert payload["startup_nonce"]


def test_wrong_attempt_never_writes_health_marker(
    tmp_path: Path,
) -> None:
    project = tmp_path / "LangLang"
    project.mkdir()
    begin_update_attempt(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )
    coordinator = StartupHealthCoordinator(
        project,
        "1.9.01.1",
        local_app_data=tmp_path / "local",
    )
    coordinator.report_auth_loop_entered()
    coordinator.report_auth_stable()
    coordinator.expect_webui()
    coordinator.report_server_started()
    coordinator.report_webui_loaded()
    coordinator.report_webui_ready()

    assert (
        read_update_health(
            project,
            local_app_data=tmp_path / "local",
        )
        == {}
    )


def test_duplicate_reports_write_marker_only_once(
    tmp_path: Path,
) -> None:
    project = tmp_path / "LangLang"
    project.mkdir()
    writes: list[tuple[str, str]] = []

    def writer(**_kwargs):
        writes.append(
            (
                str(_kwargs["version"]),
                str(_kwargs["attempt_id"]),
            )
        )
        return project / "health.json"

    coordinator = StartupHealthCoordinator(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
        attempt={
            "attempt_id": "a" * 32,
            "version": "1.9.02",
            "install_path": str(project.resolve()),
        },
        marker_writer=writer,
    )
    coordinator.report_auth_loop_entered()
    coordinator.report_auth_stable()
    coordinator.report_auth_stable()
    coordinator.report_auth_stable()

    assert writes == [("1.9.02", "a" * 32)]
