from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class UpdateAttempt:
    attempt_id: str
    version: str
    install_path: Path


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


def update_attempt_path(
    project_dir: Path,
    *,
    local_app_data: Path | None = None,
) -> Path:
    return (
        update_root_for_install(
            project_dir,
            local_app_data=local_app_data,
        )
        / "update-attempt.json"
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def begin_update_attempt(
    project_dir: Path,
    version: str,
    *,
    local_app_data: Path | None = None,
) -> UpdateAttempt:
    target = Path(project_dir).resolve()
    update_health_path(
        target,
        local_app_data=local_app_data,
    ).unlink(missing_ok=True)
    attempt = UpdateAttempt(
        attempt_id=uuid.uuid4().hex,
        version=str(version),
        install_path=target,
    )
    _write_json_atomic(
        update_attempt_path(
            target,
            local_app_data=local_app_data,
        ),
        {
            "attempt_id": attempt.attempt_id,
            "version": attempt.version,
            "install_path": str(attempt.install_path),
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    return attempt


def read_update_attempt(
    project_dir: Path,
    *,
    local_app_data: Path | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(
            update_attempt_path(
                project_dir,
                local_app_data=local_app_data,
            ).read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_update_health(
    project_dir: Path,
    version: str,
    attempt_id: str,
    startup_nonce: str = "",
    *,
    local_app_data: Path | None = None,
) -> Path:
    target = Path(project_dir).resolve()
    path = update_health_path(target, local_app_data=local_app_data)
    _write_json_atomic(
        path,
        {
            "version": str(version),
            "attempt_id": str(attempt_id),
            "startup_nonce": str(startup_nonce),
            "install_path": str(target),
            "healthy_at": datetime.now(UTC).isoformat(),
        },
    )
    return path


def mark_pending_update_healthy(
    project_dir: Path,
    version: str,
    startup_nonce: str = "",
    *,
    attempt_id: str = "",
    local_app_data: Path | None = None,
) -> Path | None:
    target = Path(project_dir).resolve()
    attempt = read_update_attempt(
        target,
        local_app_data=local_app_data,
    )
    attempt_install_path = str(attempt.get("install_path") or "").strip()
    stored_attempt_id = str(attempt.get("attempt_id") or "")
    expected_attempt_id = str(attempt_id or stored_attempt_id)
    if (
        stored_attempt_id
        and str(attempt.get("version") or "") == str(version)
        and attempt_install_path
        and Path(attempt_install_path).resolve() == target
        and expected_attempt_id == stored_attempt_id
    ):
        return write_update_health(
            target,
            version,
            stored_attempt_id,
            startup_nonce,
            local_app_data=local_app_data,
        )
    return None


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
