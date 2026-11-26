from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from weimi_manager.runtime_logging import (
    RuntimeLogManager,
    log_error,
    redact_data,
    redact_text,
)


def test_runtime_logger_creates_daily_files_session_and_run_ids(
    tmp_path: Path,
) -> None:
    fixed = datetime(2026, 11, 22, 10, 30, tzinfo=None)
    manager = RuntimeLogManager(
        tmp_path / "app",
        local_app_data=tmp_path / "local",
        now=lambda: fixed,
    )
    day_dir = tmp_path / "app" / "logs" / "2026-11-22"
    for name in (
        "app.log",
        "webui-events.jsonl",
        "network.jsonl",
        "errors.log",
    ):
        assert (day_dir / name).is_file()
    manager.app("sample", value=1)
    manager.webui_event(
        {
            "kind": "click",
            "route": "/shops",
            "control_text": "刷新",
        }
    )
    manager.network(
        action="stores.list",
        store_id=18,
        parameters={"store_id": 18},
        status="success",
        duration_ms=12.5,
        origin="manual",
    )
    manager.error("sample_error", "failed", traceback_text="Traceback")
    manager.close()

    assert (day_dir / "app.log").is_file()
    assert (day_dir / "webui-events.jsonl").is_file()
    assert (day_dir / "network.jsonl").is_file()
    assert (day_dir / "errors.log").is_file()
    session_path = day_dir / f"session-{manager.run_id}.json"
    assert session_path.is_file()
    session = json.loads(session_path.read_text(encoding="utf-8"))
    assert session["run_id"] == manager.run_id
    assert session["exit_at"]

    second = RuntimeLogManager(
        tmp_path / "app",
        local_app_data=tmp_path / "local",
        now=lambda: fixed,
    )
    assert second.run_id != manager.run_id
    second.close()


def test_sensitive_values_are_redacted_in_fields_and_error_text() -> None:
    payload = redact_data(
        {
            "password": "plain-password",
            "app_secret": "secret-value",
            "cookie": "session_id=abc",
            "authorization": "Bearer abc",
            "access_token": "token",
            "refresh_token": "refresh",
            "api_key": "key",
            "session_id": "session",
            "phone": "13800138000",
            "address": "广东省测试地址",
            "email": "tester@example.com",
            "safe": "visible",
        }
    )
    assert payload["password"] == "[REDACTED]"
    assert payload["app_secret"] == "[REDACTED]"
    assert payload["cookie"] == "[REDACTED]"
    assert payload["authorization"] == "[REDACTED]"
    assert payload["access_token"] == "[REDACTED]"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["session_id"] == "[REDACTED]"
    assert payload["phone"] == "[REDACTED]"
    assert payload["address"] == "[REDACTED]"
    assert payload["email"] == "[REDACTED]"
    assert payload["safe"] == "visible"
    text = redact_text(
        "token=abc email=tester@example.com phone=13800138000"
    )
    assert "abc" not in text
    assert "tester@example.com" not in text
    assert "13800138000" not in text


def test_log_sharding_cleanup_and_export_contains_only_logs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    old_day = root / "logs" / "2026-10-01"
    old_day.mkdir(parents=True)
    (old_day / "app.log").write_text("old\n", encoding="utf-8")
    (root / "weimi_manager.db").write_bytes(b"database")
    fixed = datetime(2026, 11, 22, 12, 0, tzinfo=None)
    manager = RuntimeLogManager(
        root,
        local_app_data=tmp_path / "local",
        retention_days=30,
        max_bytes=1024,
        now=lambda: fixed,
    )
    assert not old_day.exists()
    for index in range(80):
        manager.webui_event(
            {
                "kind": "click",
                "route": "/managed-products",
                "control_text": "测试按钮" * 8,
                "index": index,
            }
        )
    day_dir = root / "logs" / "2026-11-22"
    assert len(list(day_dir.glob("webui-events*.jsonl"))) > 1
    exported = manager.export(7)
    assert exported.is_file()
    with zipfile.ZipFile(exported) as archive:
        names = archive.namelist()
    assert all(name.startswith("logs/") for name in names)
    assert all("weimi_manager.db" not in name for name in names)
    assert any(name.endswith("app.log") for name in names)
    manager.close()


def test_read_only_project_falls_back_to_local_app_data(
    tmp_path: Path,
) -> None:
    fixed = datetime(2026, 11, 22, 12, 0, tzinfo=None)
    with mock.patch(
        "weimi_manager.runtime_logging._write_test",
        side_effect=[False, True],
    ):
        manager = RuntimeLogManager(
            tmp_path / "readonly",
            local_app_data=tmp_path / "local",
            now=lambda: fixed,
        )
    assert manager.log_root == (
        tmp_path / "local" / "LangLang" / "logs"
    ).resolve()
    manager.close()


def test_repeated_network_error_is_throttled(tmp_path: Path) -> None:
    fixed = datetime(2026, 11, 22, 12, 0, tzinfo=None)
    manager = RuntimeLogManager(
        tmp_path / "app",
        local_app_data=tmp_path / "local",
        now=lambda: fixed,
    )
    for _index in range(5):
        manager.network(
            action="stores.list",
            status="failed",
            duration_ms=3,
            error="temporary",
        )
    lines = (
        manager.day_dir / "network.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    manager.close()


def test_unhandled_style_exception_writes_traceback(
    tmp_path: Path,
) -> None:
    fixed = datetime(2026, 11, 22, 12, 0, tzinfo=None)
    manager = RuntimeLogManager(
        tmp_path / "app",
        local_app_data=tmp_path / "local",
        now=lambda: fixed,
    )
    from weimi_manager import runtime_logging

    previous = runtime_logging._manager
    runtime_logging._manager = manager
    try:
        try:
            raise RuntimeError("password=secret-value")
        except RuntimeError as exc:
            log_error("unhandled_exception", exc)
    finally:
        runtime_logging._manager = previous
    payload = json.loads(
        (manager.day_dir / "errors.log")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert payload["event"] == "unhandled_exception"
    assert "RuntimeError" in payload["traceback"]
    assert "secret-value" not in json.dumps(payload, ensure_ascii=False)
    manager.close()
