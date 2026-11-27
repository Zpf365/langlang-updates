from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.generate_stable_release import verify_release_package


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "release" / "1.9.02" / "LangLang-1.9.02-win64.zip"
MANIFEST = ROOT / "release" / "1.9.02" / "update_manifest.json"


def test_released_zip_matches_corrected_manifest_required_files() -> None:
    if not PACKAGE.is_file() or not MANIFEST.is_file():
        pytest.skip("v1.9.02 release artifacts are unavailable")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    verify_release_package(PACKAGE, manifest)
    assert "_internal/Qt6WebEngineCore.dll" in manifest["required_files"]
    assert "_internal/Qt6WebEngineWidgets.dll" in manifest["required_files"]
    assert not any(
        "PySide6/Qt6WebEngine" in path
        for path in manifest["required_files"]
    )
