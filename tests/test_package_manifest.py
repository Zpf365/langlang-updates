from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from tools.generate_stable_release import (
    build_bundle_manifest,
    build_release_manifest,
    sha256,
    source_files,
    verify_release_package,
)
from tools.package_pyside import (
    iter_source_files,
    make_program_zip,
    unsafe_program_files,
    writes_root_manifest,
)
from tools import verify_share_package as share_module
from tools.verify_share_package import verify_share
from weimi_manager import __version__
from weimi_manager import __product_version__

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
        assert embedded["sha256"] == ""


def test_external_manifest_owns_zip_hash_while_bundle_manifest_stays_blank() -> None:
    digest = "a" * 64
    bundle = build_bundle_manifest(
        download_url="https://example.invalid/app.zip",
        published_at="2026-11-23T00:00:00+00:00",
        notes="notes",
    )
    external = build_release_manifest(
        download_url="https://example.invalid/app.zip",
        package_sha256=digest,
        published_at="2026-11-23T00:00:00+00:00",
        notes="notes",
    )

    assert bundle["version"] == external["version"]
    assert bundle["sha256"] == ""
    assert external["sha256"] == digest
    assert bundle["channel"] == "stable"
    assert bundle["prerelease"] is False
    assert bundle["kind"] == "full"
    assert bundle["product_version"] == __product_version__
    assert bundle["package_version"] == __version__
    assert external["channel"] == "stable"
    assert external["prerelease"] is False
    assert external["kind"] == "full"
    assert external["product_version"] == __product_version__
    assert external["package_version"] == __version__
    assert "LangLang.exe" in external["required_files"]
    assert "_internal/webui/dist/index.html" in external["required_files"]


def test_release_package_enforces_external_hash_and_internal_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        program = root / "LangLang_1.8.82_Windows_x64"
        assets = program / "_internal" / "webui" / "dist" / "assets"
        assets.mkdir(parents=True)
        (program / "LangLang.exe").write_bytes(b"exe")
        (program / "_internal" / "webui" / "dist" / "index.html").write_text(
            "",
            encoding="utf-8",
        )
        for name in (
            "ActivityPage-test.js",
            "StoresPage-test.js",
            "OrdersParityPage-test.js",
            "ProductsPage-test.js",
            "autoRefresh-test.js",
        ):
            (assets / name).write_text("", encoding="utf-8")
        pyside = program / "_internal" / "PySide6"
        pyside.mkdir()
        (pyside / "Qt6WebEngineCore.dll").write_bytes(b"qt")
        (pyside / "Qt6WebEngineWidgets.dll").write_bytes(b"qt")
        bundle = build_bundle_manifest(
            download_url="https://example.invalid/app.zip",
            published_at="2026-11-23T00:00:00+00:00",
            notes="notes",
        )
        make_program_zip(
            program,
            root / "package.zip",
            bundle_manifest=bundle,
        )
        digest = sha256(root / "package.zip")
        manifest = build_release_manifest(
            download_url="https://example.invalid/app.zip",
            package_sha256=digest,
            published_at="2026-11-23T00:00:00+00:00",
            notes="notes",
        )

        verify_release_package(root / "package.zip", manifest)

        manifest["sha256"] = "0" * 64
        with pytest.raises(RuntimeError, match="SHA256"):
            verify_release_package(root / "package.zip", manifest)


def test_browser_runtime_icu_data_is_allowed_but_root_icu_is_not() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        program = Path(tmp) / "LangLang"
        browser = program / "_internal" / "browser_runtime" / "chromium"
        browser.mkdir(parents=True)
        (browser / "icudtl.dat").write_bytes(b"browser")
        pyside_resources = program / "_internal" / "PySide6" / "resources"
        pyside_resources.mkdir(parents=True)
        (pyside_resources / "icudtl.dat").write_bytes(b"qt")
        assert unsafe_program_files(program) == []

        (program / "icudtl.dat").write_bytes(b"unexpected")
        assert unsafe_program_files(program) == [program / "icudtl.dat"]


def test_test_update_policy_is_never_packaged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        program = root / "LangLang"
        internal = program / "_internal"
        internal.mkdir(parents=True)
        (program / "LangLang.exe").write_bytes(b"exe")
        policy = internal / "test-update-policy.json"
        policy.write_text("{}", encoding="utf-8")
        target = root / "share.zip"

        make_program_zip(program, target)

        with zipfile.ZipFile(target) as archive:
            assert all(
                "test-update-policy.json" not in name
                for name in archive.namelist()
            )
        assert policy in unsafe_program_files(program)


def test_program_zip_excludes_tests_docs_source_and_source_maps() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        program = root / "LangLang"
        (program / "_internal").mkdir(parents=True)
        (program / "LangLang.exe").write_bytes(b"exe")
        (program / "tests").mkdir()
        (program / "tests" / "test_runtime.py").write_text("x", encoding="utf-8")
        (program / "docs").mkdir()
        (program / "docs" / "developer.md").write_text("x", encoding="utf-8")
        (program / "README.md").write_text("x", encoding="utf-8")
        (program / "_internal" / "app.js.map").write_text("{}", encoding="utf-8")
        target = root / "public.zip"

        make_program_zip(program, target)

        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
        assert "LangLang/LangLang.exe" in names
        assert not any(name.endswith(".map") for name in names)
        assert not any("/tests/" in name for name in names)
        assert not any("/docs/" in name for name in names)
        assert "LangLang/README.md" not in names
        assert unsafe_program_files(program)


def test_public_package_scripts_require_internal_source_opt_in() -> None:
    package_script = (
        ROOT / "tools" / "package_pyside.py"
    ).read_text(encoding="utf-8")
    release_script = (
        ROOT / "tools" / "generate_stable_release.py"
    ).read_text(encoding="utf-8")
    assert '"--include-internal-source"' in package_script
    assert "if args.include_internal_source" in package_script
    assert '"--include-internal-source"' in release_script
    assert "if args.include_internal_source" in release_script


def test_internal_source_archive_contains_webui_sources_without_dependencies() -> None:
    package_source_paths = {
        path.as_posix()
        for path in iter_source_files()
    }
    assert "webui/src/components/UploadStoreSelectionDialog.vue" in package_source_paths
    assert "webui/src/pages/StoresPage.vue" in package_source_paths
    assert "webui/package.json" in package_source_paths
    assert not any("/node_modules/" in path for path in package_source_paths)
    assert not any(path.startswith("webui/dist/") for path in package_source_paths)
    release_sources = {
        path.as_posix()
        for path in source_files()
    }
    assert not any(
        path.startswith("isolated/")
        for path in release_sources
    )


def test_share_zip_keeps_public_auth_config_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        program = root / "LangLang"
        assets = program / "_internal" / "assets"
        assets.mkdir(parents=True)
        (program / "LangLang.exe").write_bytes(b"exe")
        (assets / "auth_config.json").write_text(
            '{"supabase_url":"https://example.supabase.co"}',
            encoding="utf-8",
        )
        (assets / "tester_accounts.json").write_text("{}", encoding="utf-8")
        (assets / "universal_login.json").write_text("{}", encoding="utf-8")
        (program / "auth_config.json").write_text(
            '{"private":"sidecar"}',
            encoding="utf-8",
        )
        target = root / "share.zip"

        make_program_zip(program, target)

        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
        assert "LangLang/_internal/assets/auth_config.json" in names
        assert "LangLang/auth_config.json" not in names
        assert "LangLang/_internal/assets/tester_accounts.json" not in names
        assert "LangLang/_internal/assets/universal_login.json" not in names


def test_share_mode_never_overwrites_root_manifest(tmp_path: Path) -> None:
    assert not writes_root_manifest(
        tmp_path,
        share_only=True,
    )
    assert not writes_root_manifest(
        tmp_path,
        share_only=False,
    )


def test_share_package_rejects_logs_accounts_and_test_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        package = root / "share.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("LangLang/LangLang.exe", b"exe")
            archive.writestr(
                "LangLang/_internal/webui/dist/index.html",
                b"<html></html>",
            )
            archive.writestr(
                "LangLang/_internal/update_manifest.json",
                json.dumps(
                    {
                        "version": __version__,
                        "channel": "stable",
                        "prerelease": False,
                        "download_url": "",
                        "sha256": "",
                    }
                ),
            )
            archive.writestr(
                "LangLang/_internal/assets/auth_config.json",
                json.dumps(
                    {
                        "supabase_url": "https://example.supabase.co",
                        "publishable_key": "public-key",
                    }
                ),
            )
            rust_bytes = b"test-rust-binary"
            archive.writestr(
                "LangLang/_internal/cryptography/hazmat/bindings/_rust.pyd",
                rust_bytes,
            )
        with mock.patch.object(
            share_module,
            "EXPECTED_RUST_SHA256",
            hashlib.sha256(rust_bytes).hexdigest(),
        ):
            verify_share(package)

        with zipfile.ZipFile(package, "a") as archive:
            archive.writestr(
                "LangLang/logs/2026-11-23/app.log",
                b"private",
            )
            archive.writestr(
                "LangLang/_internal/test-update-policy.json",
                b"{}",
            )
            archive.writestr(
                "LangLang/_internal/saved_accounts.bin",
                b"private",
            )

        with pytest.raises(RuntimeError, match="禁止文件"):
            verify_share(package)


def test_stable_source_package_includes_buyer_assistant_sources() -> None:
    files = {path.as_posix() for path in source_files()}

    assert "buyer_miniprogram/app.json" in files
    assert "buyer_miniprogram/pages/bind/index.js" in files
    assert "supabase/functions/buyer-assistant-mini/index.ts" in files
    assert "supabase/migrations/202611190001_buyer_assistant.sql" in files
    assert "DONGXIAO_1_8_49_WECHAT_CONFIG_CN.md" in files
    assert "buyer_assistant.env.example" in files
    assert "HANDOFF_1.6.62.md" not in files
    assert not any(
        "LocalAppData" in path
        or path.casefold().endswith(
            (".db", ".sqlite", ".sqlite3", ".bak", ".log", ".jsonl", ".bin")
        )
        for path in files
    )


def test_windows_build_uses_full_dependency_spec() -> None:
    script = (ROOT / "tools" / "build_release.ps1").read_text(
        encoding="utf-8"
    )

    assert "LangLang.spec" in script
    assert '"launcher.py"' not in script
    assert "PyInstaller failed with exit code" in script
