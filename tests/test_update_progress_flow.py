from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from unittest import mock

from weimi_manager.updater import (
    UpdateInfo,
    check_for_update,
    download_and_stage_update,
)
from weimi_manager.ui.update_progress import format_update_progress


def test_stage_update_reports_real_download_verify_and_extract_progress(
    tmp_path: Path,
) -> None:
    root = tmp_path
    project = root / "dist" / "LangLang"
    project.mkdir(parents=True)
    package = root / "LangLang-9.0.4.zip"
    payload = b"0123456789" * 8192
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("LangLang/LangLang.exe", payload)
        archive.writestr(
            "LangLang/_internal/update_manifest.json",
            json.dumps({"version": "9.0.4", "sha256": ""}),
        )
        archive.writestr(
            "LangLang/_internal/webui/dist/index.html",
            b"<html></html>",
        )
        for name in (
            "ActivityPage-test.js",
            "StoresPage-test.js",
            "OrdersParityPage-test.js",
            "ProductsPage-test.js",
            "autoRefresh-test.js",
        ):
            archive.writestr(
                f"LangLang/_internal/webui/dist/assets/{name}",
                b"asset",
            )
        for name in (
            "Qt6WebEngineCore.dll",
            "Qt6WebEngineWidgets.dll",
        ):
            archive.writestr(
                f"LangLang/_internal/PySide6/{name}",
                b"qt",
            )
    info = UpdateInfo(
        current_version="9.0.3",
        latest_version="9.0.4",
        available=True,
        download_url=str(package),
        sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
    )
    events: list[tuple[str, int, int]] = []
    with mock.patch.dict(
        "os.environ",
        {"LOCALAPPDATA": str(root / "local")},
    ):
        staged = download_and_stage_update(
            info,
            project,
            progress=lambda message, current, total: events.append(
                (message, current, total)
            ),
        )

    assert staged.version == "9.0.4"
    assert any("下载" in message for message, _current, _total in events)
    assert any("SHA256" in message for message, _current, _total in events)
    assert any("解压" in message for message, _current, _total in events)
    assert any(
        total > 0 and 0 < current <= total
        for _message, current, total in events
    )


def test_progress_format_distinguishes_known_and_unknown_totals() -> None:
    stage, detail, determinate, percentage = format_update_progress(
        "正在下载更新包",
        512 * 1024,
        2 * 1024 * 1024,
    )
    assert stage == "正在下载更新包"
    assert "512.0 KB / 2.0 MB" in detail
    assert "25%" in detail
    assert determinate is True
    assert percentage == 25.0

    _stage, detail, determinate, percentage = format_update_progress(
        "正在下载更新包",
        3 * 1024 * 1024,
        0,
    )
    assert detail == "已下载 3.0 MB"
    assert determinate is False
    assert percentage == 0.0


def test_update_source_environment_override_supports_isolated_testing(
    tmp_path: Path,
) -> None:
    package = tmp_path / "LangLang-9.0.5.zip"
    package.write_bytes(b"isolated-update")
    manifest = tmp_path / "update_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "9.0.5",
                "download_url": package.name,
                "sha256": hashlib.sha256(b"isolated-update").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    with mock.patch.dict(
        "os.environ",
        {"LANGLANG_UPDATE_SOURCE": str(manifest)},
    ):
        info = check_for_update("", tmp_path)
    assert info.latest_version == "9.0.5"
    assert info.download_url == str(package.resolve())
