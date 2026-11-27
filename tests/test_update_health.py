from __future__ import annotations

from pathlib import Path

from weimi_manager.update_health import (
    read_update_health,
    write_update_health,
)


def test_update_health_marker_records_version_atomically(
    tmp_path: Path,
) -> None:
    project = tmp_path / "LangLang"
    project.mkdir()

    path = write_update_health(
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
    assert payload["install_path"] == str(project.resolve())
    assert not list(path.parent.glob("*.tmp"))
