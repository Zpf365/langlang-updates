from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from tools import fetch_diagnostic_reports as diagnostics
from weimi_manager import __version__


def test_redaction_removes_credentials_identity_and_contacts() -> None:
    payload = {
        "username": "zpfly",
        "token": "secret",
        "nested": {
            "message": (
                "Authorization: Bearer abc.def "
                "wx1234567890abcdef "
                "13812345678 user@example.com "
                + "a" * 64
            ),
            "address": "某地址",
        },
    }

    result = diagnostics.redact_value(payload)

    assert result["username"] == "[REDACTED]"
    assert result["token"] == "[REDACTED]"
    assert result["nested"]["address"] == "[REDACTED]"
    message = result["nested"]["message"]
    assert "zpfly" not in message
    assert "Bearer [REDACTED]" in message
    assert "[REDACTED_APPID]" in message
    assert "[REDACTED_PHONE]" in message
    assert "[REDACTED_EMAIL]" in message
    assert "[REDACTED_HEX]" in message


def test_classification_distinguishes_current_fixed_and_historical() -> None:
    current, _ = diagnostics.classify_report(
        {
            "version": __version__,
            "category": "crash",
            "message": "boom",
        }
    )
    fixed, reason = diagnostics.classify_report(
        {
            "version": "1.8.41",
            "category": "tk_callback",
            "message": "KeyError: 'popdown'",
        }
    )
    historical, _ = diagnostics.classify_report(
        {
            "version": "1.8.50",
            "category": "ui_stall",
            "message": "界面主线程无响应约 3 秒",
        }
    )

    assert current == "still_present"
    assert fixed == "fixed"
    assert "WebUI" in reason
    assert historical == "historical"


def test_current_high_severity_report_blocks_release_gate(
    tmp_path: Path,
) -> None:
    result = diagnostics.write_review(
        [
            {
                "version": __version__,
                "category": "crash",
                "severity": "fatal",
                "message": "进程崩溃",
                "created_at": "2026-11-23T00:00:00+00:00",
            }
        ],
        tmp_path / "review.md",
    )

    assert result["high_risk"] == 1
    assert "禁止发布" in (tmp_path / "review.md").read_text(
        encoding="utf-8"
    )


def test_saved_administrator_token_is_obtained_from_auth_service() -> None:
    saved = SimpleNamespace(username="zpfly")
    store = SimpleNamespace(
        list_accounts=lambda: [saved],
        find=lambda username: saved,
    )
    service = SimpleNamespace(
        store=store,
        restore_account=lambda account: SimpleNamespace(
            tokens=SimpleNamespace(access_token="admin-token")
        ),
    )
    paths = SimpleNamespace(
        data_dir=Path("C:/isolated/LangLang/data")
    )

    with (
        mock.patch.object(
            diagnostics.AuthConfig,
            "load",
            return_value=object(),
        ),
        mock.patch.object(
            diagnostics,
            "build_auth_service",
            return_value=service,
        ),
    ):
        token = diagnostics.saved_admin_token(paths, "zpfly")

    assert token == "admin-token"


def test_main_automatically_uses_saved_token_when_env_is_absent(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return [
            {
                "version": "1.8.50",
                "category": "session_end",
                "severity": "info",
                "message": "软件正常关闭",
            }
        ]

    with (
        mock.patch.dict(
            os.environ,
            {
                "LANGLANG_DIAGNOSTIC_ADMIN_TOKEN": "",
                "LANGLANG_DIAGNOSTIC_SERVICE_ROLE_KEY": "",
                "SUPABASE_SERVICE_ROLE_KEY": "",
            },
        ),
        mock.patch.object(
            diagnostics,
            "resolve_paths",
            return_value=object(),
        ),
        mock.patch.object(
            diagnostics,
            "saved_admin_token",
            return_value="auto-admin-token",
        ),
        mock.patch.object(
            diagnostics,
            "_fetch",
            side_effect=fake_fetch,
        ),
    ):
        code = diagnostics.main(
            [
                "--output",
                str(tmp_path / "review.md"),
                "--raw-output",
                str(tmp_path / "raw.json"),
            ]
        )

    assert code == 0
    assert captured["token"] == "auto-admin-token"
    assert "auto-admin-token" not in (
        tmp_path / "raw.json"
    ).read_text(encoding="utf-8")
    assert "auto-admin-token" not in (
        tmp_path / "review.md"
    ).read_text(encoding="utf-8")


def test_raw_diagnostics_cannot_be_written_to_release() -> None:
    with pytest.raises(ValueError, match="release"):
        diagnostics._safe_raw_path(
            diagnostics.ROOT / "release" / "diagnostics.json",
            diagnostics.ROOT / "artifacts" / "diagnostic-reports",
        )
