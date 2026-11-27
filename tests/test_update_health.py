from __future__ import annotations

from pathlib import Path

from weimi_manager.update_health import (
    begin_update_attempt,
    mark_pending_update_healthy,
    read_update_attempt,
    read_update_health,
    update_health_path,
)


def test_update_health_marker_records_attempt_nonce_atomically(
    tmp_path: Path,
) -> None:
    project = tmp_path / "LangLang"
    project.mkdir()
    health_path = update_health_path(
        project,
        local_app_data=tmp_path / "local",
    )
    health_path.parent.mkdir(parents=True)
    health_path.write_text(
        '{"version":"old","attempt_id":"stale"}',
        encoding="utf-8",
    )

    attempt = begin_update_attempt(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )
    assert not health_path.exists()
    assert (
        read_update_attempt(
            project,
            local_app_data=tmp_path / "local",
        )["attempt_id"]
        == attempt.attempt_id
    )

    path = mark_pending_update_healthy(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )
    payload = read_update_health(
        project,
        local_app_data=tmp_path / "local",
    )

    assert path.is_file()
    assert payload["version"] == "1.9.02"
    assert payload["attempt_id"] == attempt.attempt_id
    assert payload["install_path"] == str(project.resolve())
    assert not list(path.parent.glob("*.tmp"))


def test_pending_health_rejects_version_mismatch(
    tmp_path: Path,
) -> None:
    project = tmp_path / "LangLang"
    project.mkdir()
    begin_update_attempt(
        project,
        "1.9.02",
        local_app_data=tmp_path / "local",
    )

    path = mark_pending_update_healthy(
        project,
        "1.9.01.1",
        local_app_data=tmp_path / "local",
    )

    assert path is None
    assert (
        read_update_health(
            project,
            local_app_data=tmp_path / "local",
        )
        == {}
    )
