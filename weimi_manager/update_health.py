from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _local_app_data(local_app_data: Path | None = None) -> Path:
    return Path(
        local_app_data or os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    )


def update_root_for_install(
    project_dir: Path,
    *,
    local_app_data: Path | None = None,
) -> Path:
    target = Path(project_dir).resolve()
    key = hashlib.sha256(os.path.normcase(str(target)).encode("utf-8")).hexdigest()[:16]
    return _local_app_data(local_app_data) / "LangLang" / "updates" / key


def update_health_path(
    project_dir: Path,
    *,
    local_app_data: Path | None = None,
) -> Path:
    return (
        update_root_for_install(
            project_dir,
            local_app_data=local_app_data,
        )
        / "health.json"
    )


def write_update_health(
    project_dir: Path,
    version: str,
    *,
    local_app_data: Path | None = None,
) -> Path:
    target = Path(project_dir).resolve()
    path = update_health_path(target, local_app_data=local_app_data)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": str(version),
        "install_path": str(target),
        "healthy_at": datetime.now(UTC).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def read_update_health(
    project_dir: Path,
    *,
    local_app_data: Path | None = None,
) -> dict[str, Any]:
    path = update_health_path(
        project_dir,
        local_app_data=local_app_data,
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
