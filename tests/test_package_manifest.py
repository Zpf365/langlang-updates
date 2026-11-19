from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from tools.generate_stable_release import source_files
from tools.package_pyside import make_program_zip, unsafe_program_files

ROOT = Path(__file__).resolve().parent.parent


def test_release_source_has_public_auth_config() -> None:
    path = ROOT / "assets" / "auth_config.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert str(payload.get("supabase_url") or "").startswith("https://")
    key = str(
        payload.get("publishable_key")
        or payload.get("anon_key")
        or ""
    )
    assert key and "YOUR_" not in key.upper()


def test_program_zip_replaces_stale_embedded_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        program = root / "LangLang"
        internal = program / "_internal"
        internal.mkdir(parents=True)
        (program / "LangLang.exe").write_bytes(b"exe")
        (internal / "update_manifest.json").write_text(
            json.dumps({"version": "1.8.2"}),
            encoding="utf-8",
        )
        target = root / "package.zip"

        make_program_zip(
            program,
            target,
            bundle_manifest={
                "version": "1.8.3",
                "download_url": "",
                "sha256": "",
                "mandatory": False,
            },
        )

        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            manifest_name = "LangLang/_internal/update_manifest.json"
            assert names.count(manifest_name) == 1
            embedded = json.loads(archive.read(manifest_name))
        assert embedded["version"] == "1.8.3"


def test_browser_runtime_icu_data_is_allowed_but_root_icu_is_not() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        program = Path(tmp) / "LangLang"
        browser = program / "_internal" / "browser_runtime" / "chromium"
        browser.mkdir(parents=True)
        (browser / "icudtl.dat").write_bytes(b"browser")
        assert unsafe_program_files(program) == []

        (program / "icudtl.dat").write_bytes(b"unexpected")
        assert unsafe_program_files(program) == [program / "icudtl.dat"]


def test_stable_source_package_includes_buyer_assistant_sources() -> None:
    files = {path.as_posix() for path in source_files()}

    assert "buyer_miniprogram/app.json" in files
    assert "buyer_miniprogram/pages/bind/index.js" in files
    assert "supabase/functions/buyer-assistant-mini/index.ts" in files
    assert "supabase/migrations/202611190001_buyer_assistant.sql" in files
    assert "DONGXIAO_1_8_49_WECHAT_CONFIG_CN.md" in files
    assert "buyer_assistant.env.example" in files


def test_windows_build_uses_full_dependency_spec() -> None:
    script = (ROOT / "tools" / "build_release.ps1").read_text(
        encoding="utf-8"
    )

    assert "LangLang.spec" in script
    assert '"launcher.py"' not in script
