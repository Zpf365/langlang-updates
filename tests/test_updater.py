from __future__ import annotations

import hashlib
import http.client
import json
import msvcrt
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from weimi_manager import updater as updater_module
from weimi_manager.ui.app import MainWindow
from weimi_manager.updater import (
    DEFAULT_UPDATE_SOURCE,
    StagedUpdate,
    UpdateInfo,
    _download_archive,
    _github_manifest_via_latest_redirect,
    _get_with_retries,
    _requests_session,
    _version_tuple,
    check_for_update,
    download_and_stage_update,
    is_development_preview_version,
    launch_update_installer,
)


def write_update_package(
    package: Path,
    version: str,
    *,
    included_version: str | None = None,
    embedded_sha256: str = "",
    include_webui: bool = True,
) -> None:
    assets = {
        "ActivityPage-test.js",
        "StoresPage-test.js",
        "OrdersParityPage-test.js",
        "ProductsPage-test.js",
        "autoRefresh-test.js",
    }
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("LangLang/LangLang.exe", b"exe")
        archive.writestr(
            "LangLang/_internal/update_manifest.json",
            json.dumps(
                {
                    "version": included_version or version,
                    "sha256": embedded_sha256,
                }
            ),
        )
        if include_webui:
            archive.writestr(
                "LangLang/_internal/webui/dist/index.html",
                b"<html></html>",
            )
            for name in assets:
                archive.writestr(
                    f"LangLang/_internal/webui/dist/assets/{name}",
                    b"asset",
                )
        archive.writestr(
            "LangLang/_internal/PySide6/Qt6WebEngineCore.dll",
            b"qt",
        )
        archive.writestr(
            "LangLang/_internal/PySide6/Qt6WebEngineWidgets.dll",
            b"qt",
        )


class UpdaterTests(unittest.TestCase):
    def test_internal_four_part_versions_compare_numerically(self) -> None:
        self.assertLess(
            _version_tuple("1.8.93.1"),
            _version_tuple("1.8.93.2"),
        )
        self.assertLess(
            _version_tuple("1.8.93.9"),
            _version_tuple("1.8.94"),
        )
        self.assertGreater(
            _version_tuple("1.8.94.1"),
            _version_tuple("1.8.94"),
        )

    def test_update_package_accepts_four_part_internal_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            package = root / "LangLang-1.8.93.2-win64.zip"
            write_update_package(package, "1.8.93.2")
            info = UpdateInfo(
                current_version="1.8.93.1",
                latest_version="1.8.93.2",
                available=True,
                download_url=str(package),
                sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            )
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                staged = download_and_stage_update(info, project)
        self.assertEqual(staged.version, "1.8.93.2")

    def test_download_resumes_after_incomplete_read(self) -> None:
        payload = b"langlang-update" * 1024
        split = len(payload) // 2
        calls: list[dict[str, str]] = []

        class Response:
            def __init__(self, start: int, fail_after: int | None) -> None:
                self.start = start
                self.fail_after = fail_after
                self.headers = {
                    "Content-Length": str(len(payload) - start),
                    "Content-Range": (
                        f"bytes {start}-{len(payload) - 1}/{len(payload)}"
                        if start
                        else ""
                    ),
                }
                self.status_code = 206 if start else 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, _chunk_size):
                remaining = payload[self.start :]
                if self.fail_after is None:
                    yield remaining
                    return
                yield remaining[: self.fail_after]
                raise http.client.IncompleteRead(
                    remaining[self.fail_after :],
                    len(remaining) - self.fail_after,
                )

        responses = [
            Response(0, split),
            Response(split, None),
        ]

        def fake_get(
            _session,
            _url,
            *,
            headers=None,
            **_kwargs,
        ):
            calls.append(dict(headers or {}))
            return responses.pop(0)

        info = UpdateInfo(
            current_version="1.7.6",
            latest_version="1.7.7",
            available=True,
            download_url="https://example.invalid/update.zip",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "weimi_manager.updater._get_with_retries",
            side_effect=fake_get,
        ):
            destination = Path(tmp) / "update.zip"
            _download_archive(info, destination, None)
            downloaded = destination.read_bytes()

        self.assertEqual(downloaded, payload)
        self.assertEqual(calls[1]["Range"], f"bytes={split}-")

    def test_resume_accepts_range_response_without_content_range_header(
        self,
    ) -> None:
        payload = b"resumed-update-package" * 1024
        split = len(payload) // 2

        class Response:
            status_code = 206

            def __init__(self) -> None:
                self.headers = {
                    "Content-Length": str(len(payload) - split),
                }

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, _chunk_size):
                yield payload[split:]

        info = UpdateInfo(
            current_version="1.8.8",
            latest_version="1.8.9",
            available=True,
            download_url="https://example.invalid/update.zip",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "update.zip"
            destination.with_suffix(".zip.part").write_bytes(payload[:split])
            with mock.patch(
                "weimi_manager.updater._get_with_retries",
                return_value=Response(),
            ):
                _download_archive(info, destination, None)
            self.assertEqual(destination.read_bytes(), payload)

    @unittest.skipUnless(os.name == "nt", "Windows file locking test")
    def test_download_ignores_locked_shared_part_file(self) -> None:
        payload = b"isolated-part-file" * 1024

        class Response:
            status_code = 200
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, _chunk_size):
                yield payload

        info = UpdateInfo(
            current_version="1.8.11",
            latest_version="1.8.12",
            available=True,
            download_url="https://example.invalid/update.zip",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "update.zip"
            shared_part = destination.with_suffix(".zip.part")
            shared_part.write_bytes(b"x")
            with shared_part.open("r+b") as locked:
                locked.seek(0)
                msvcrt.locking(locked.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    with mock.patch(
                        "weimi_manager.updater._get_with_retries",
                        return_value=Response(),
                    ):
                        _download_archive(info, destination, None)
                finally:
                    locked.seek(0)
                    msvcrt.locking(locked.fileno(), msvcrt.LK_UNLCK, 1)
            self.assertEqual(destination.read_bytes(), payload)

    def test_download_accepts_complete_partial_on_range_not_satisfiable(
        self,
    ) -> None:
        payload = b"complete-update-package"
        info = UpdateInfo(
            current_version="1.7.6",
            latest_version="1.7.7",
            available=True,
            download_url="https://example.invalid/update.zip",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "update.zip"
            destination.with_suffix(".zip.part").write_bytes(payload)
            error = updater_module.requests.HTTPError(
                response=SimpleNamespace(status_code=416)
            )
            with mock.patch(
                "weimi_manager.updater._get_with_retries",
                side_effect=error,
            ):
                _download_archive(info, destination, None)
            self.assertEqual(destination.read_bytes(), payload)

    def test_development_version_does_not_use_production_channel(self) -> None:
        self.assertTrue(is_development_preview_version("0.6.53"))
        self.assertFalse(is_development_preview_version("1.0.0"))
        self.assertFalse(is_development_preview_version("1.6.51"))

    def test_development_preview_skips_production_update_check(self) -> None:
        window = SimpleNamespace(
            _update_check_running=False,
            set_status=mock.Mock(),
            paths=SimpleNamespace(resource_dir=Path("D:/GPT/no-tester-channel")),
            db=SimpleNamespace(get_setting=lambda *_args: ""),
        )

        with mock.patch(
            "weimi_manager.ui.app.__version__",
            "0.6.53",
        ):
            MainWindow._check_updates(window, manual=True)

        window.set_status.assert_called_once()
        self.assertIn("测试版", window.set_status.call_args.args[0])

    def test_installer_confirmation_precedes_installer_launch(self) -> None:
        events: list[str] = []

        class FakeWindow:
            def __init__(self) -> None:
                self.paths = SimpleNamespace(
                    project_dir=Path("D:/GPT/test-program/LangLang")
                )
                self.captured: dict[str, object] = {}
                self.db = SimpleNamespace(
                    start_task_log=lambda **_kwargs: 1,
                )

            def set_status(self, *_args, **_kwargs) -> None:
                return

            def _finish_task_log(self, *_args, **_kwargs) -> None:
                return

            def after(self, _delay, _callback) -> None:
                events.append("after")

            def destroy(self) -> None:
                return

            def run_task(
                self,
                work,
                on_done,
                on_error,
                progress=None,
                log_title="",
            ) -> bool:
                self.captured.update(
                    {
                        "work": work,
                        "on_done": on_done,
                        "on_error": on_error,
                    }
                )
                return True

        info = UpdateInfo(
            current_version="1.6.28",
            latest_version="1.6.29",
            available=True,
            download_url="https://example.invalid/update.zip",
            sha256="0" * 64,
        )
        window = FakeWindow()
        MainWindow._install_update(window, info)

        def fake_launch(*_args, **_kwargs):
            events.append("launch")
            return Path("D:/GPT/update.log")

        with (
            mock.patch(
                "weimi_manager.ui.app.messagebox.showinfo",
                side_effect=lambda *_args, **_kwargs: events.append("confirm"),
            ),
            mock.patch(
                "weimi_manager.ui.app.launch_update_installer",
                side_effect=fake_launch,
            ),
            mock.patch(
                "weimi_manager.ui.app.threading.Timer",
            ),
        ):
            window.captured["on_done"](
                SimpleNamespace(version="1.6.29")
            )

        self.assertEqual(events, ["confirm", "launch", "after"])

    def test_update_session_uses_windows_system_proxy_by_default(self) -> None:
        session = _requests_session()
        self.assertTrue(session.trust_env)

    def test_github_latest_redirect_resolves_fresh_tag_manifest(self) -> None:
        class Response:
            status_code = 302
            headers = {
                "Location": (
                    "https://github.com/Zpf365/langlang-updates/"
                    "releases/tag/v1.8.82"
                )
            }

        class ManifestResponse:
            @staticmethod
            def json():
                return {
                    "version": "1.8.82",
                    "channel": "stable",
                    "prerelease": False,
                }

        class Session:
            def __init__(self) -> None:
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        session = Session()
        with (
            mock.patch.object(
                updater_module,
                "_requests_session",
                return_value=session,
            ),
            mock.patch.object(
                updater_module,
                "_get_with_retries",
                return_value=ManifestResponse(),
            ) as get_manifest,
        ):
            manifest = _github_manifest_via_latest_redirect(
                DEFAULT_UPDATE_SOURCE
            )

        self.assertEqual(manifest["version"], "1.8.82")
        self.assertEqual(manifest["channel"], "stable")
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertIn(
            "/releases/download/v1.8.82/update_manifest.json",
            get_manifest.call_args.args[1],
        )

    def test_transient_connection_error_is_retried(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return

        class FakeSession:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise updater_module.requests.ConnectionError("temporary")
                return FakeResponse()

        session = FakeSession()
        with mock.patch.object(updater_module.time, "sleep") as sleep:
            response = _get_with_retries(session, "https://example.invalid")
        self.assertIsInstance(response, FakeResponse)
        self.assertEqual(session.calls, 2)
        sleep.assert_called_once_with(1)

    def test_manifest_supports_optional_and_mandatory_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "LangLang_9.0.0.zip"
            package.write_bytes(b"package")
            manifest = root / "update_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": "9.0.0",
                        "download_url": package.name,
                        "sha256": hashlib.sha256(b"package").hexdigest(),
                        "notes": "重要修复",
                        "mandatory": True,
                        "channel": "stable",
                        "prerelease": False,
                        "required_files": [
                            "LangLang.exe",
                            "_internal/webui/dist/index.html",
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            info = check_for_update(str(manifest), root)
            self.assertTrue(info.available)
            self.assertTrue(info.mandatory)
            self.assertEqual(
                info.download_url,
                str(package.resolve()),
            )
            self.assertEqual(info.notes, "重要修复")
            self.assertEqual(info.channel, "stable")
            self.assertFalse(info.prerelease)
            self.assertEqual(
                info.required_files,
                (
                    "LangLang.exe",
                    "_internal/webui/dist/index.html",
                ),
            )

    def test_update_check_can_compare_against_target_install_version(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "update_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": "1.8.82",
                        "channel": "stable",
                        "prerelease": False,
                    }
                ),
                encoding="utf-8",
            )
            info = check_for_update(
                str(manifest),
                Path(tmp),
                current_version="1.8.80",
            )
        self.assertEqual(info.current_version, "1.8.80")
        self.assertTrue(info.available)

    def test_stage_update_requires_expected_version_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            package = root / "LangLang_9.0.1.zip"
            write_update_package(package, "9.0.1")
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            info = UpdateInfo(
                current_version="1.0.0",
                latest_version="9.0.1",
                available=True,
                download_url=str(package),
                sha256=digest,
            )
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                staged = download_and_stage_update(
                    info,
                    project,
                )
            self.assertEqual(staged.version, "9.0.1")
            self.assertTrue(
                (staged.package_root / "LangLang.exe").is_file()
            )
            self.assertIn(
                "LangLang-9.0.1.zip",
                str(staged.archive_path),
            )

    def test_stage_update_rejects_package_without_webui_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            package = root / "LangLang_9.0.11.zip"
            write_update_package(
                package,
                "9.0.11",
                include_webui=False,
            )
            info = UpdateInfo(
                current_version="1.0.0",
                latest_version="9.0.11",
                available=True,
                download_url=str(package),
                sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            )
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "webui/dist/index.html",
                ):
                    download_and_stage_update(info, project)

    def test_stage_update_rejects_embedded_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            package = root / "LangLang_9.0.12.zip"
            write_update_package(
                package,
                "9.0.12",
                included_version="9.0.11",
            )
            info = UpdateInfo(
                current_version="1.0.0",
                latest_version="9.0.12",
                available=True,
                download_url=str(package),
                sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            )
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                with self.assertRaisesRegex(ValueError, "版本"):
                    download_and_stage_update(info, project)

    def test_embedded_manifest_sha_is_not_used_to_validate_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            package = root / "LangLang_9.0.13.zip"
            write_update_package(
                package,
                "9.0.13",
                embedded_sha256="f" * 64,
            )
            package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
            info = UpdateInfo(
                current_version="1.0.0",
                latest_version="9.0.13",
                available=True,
                download_url=str(package),
                sha256=package_sha,
            )
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                staged = download_and_stage_update(info, project)
            bundled = json.loads(
                (
                    staged.package_root
                    / "_internal"
                    / "update_manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(bundled["sha256"], "f" * 64)
            self.assertNotEqual(bundled["sha256"], package_sha)

    def test_bad_sha256_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            package = root / "LangLang_9.0.2.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("LangLang/LangLang.exe", b"exe")
            info = UpdateInfo(
                current_version="1.0.0",
                latest_version="9.0.2",
                available=True,
                download_url=str(package),
                sha256="0" * 64,
            )
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                with self.assertRaises(ValueError):
                    download_and_stage_update(info, project)

    def test_installer_process_is_not_detached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            (project / "LangLang.exe").write_bytes(b"old")
            package_root = root / "stage" / "LangLang"
            package_root.mkdir(parents=True)
            (package_root / "LangLang.exe").write_bytes(b"new")
            staged = StagedUpdate(
                version="9.0.3",
                archive_path=root / "package.zip",
                staging_root=root / "stage",
                package_root=package_root,
            )
            captured = {}

            def fake_popen(command, **kwargs):
                captured["command"] = command
                captured["flags"] = kwargs.get("creationflags", 0)
                captured["cwd"] = kwargs.get("cwd")
                return object()

            with mock.patch.object(
                updater_module.subprocess,
                "Popen",
                fake_popen,
            ), mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                log_path = launch_update_installer(staged, project)
            script = (
                log_path.parent / "install-update.ps1"
            ).read_text(encoding="utf-8-sig")
            self.assertIn("Invoke-WithRetry", script)
            self.assertIn("Copy-TreeWithRetry", script)
            self.assertIn("in-place update", script)
            self.assertNotIn("update-new-", script)
            self.assertIn("Rollback copy:", script)
            self.assertIn("Verified rollback copy removed", script)
            self.assertIn("restore previous version", script)
            self.assertFalse(
                captured["flags"]
                & getattr(subprocess, "DETACHED_PROCESS", 0)
            )
            self.assertTrue(
                captured["flags"]
                & getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            self.assertEqual(
                Path(captured["cwd"]).resolve(),
                (root / "local" / "LangLang" / "updates").resolve(),
            )
            self.assertIn('Get-Process -Name "LangLang"', script)
            self.assertIn("Waiting for target processes", script)
            self.assertIn(
                "Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8",
                script,
            )

    @unittest.skipUnless(os.name == "nt", "Windows installer test")
    def test_installer_restores_old_version_when_new_start_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "program" / "LangLang"
            project.mkdir(parents=True)
            target_exe = project / "LangLang.exe"
            target_exe.write_bytes(b"old-version")
            preserved_log = project / "logs" / "2026-11-22" / "app.log"
            preserved_log.parent.mkdir(parents=True)
            preserved_log.write_text("pre-update log\n", encoding="utf-8")

            package_root = root / "stage" / "LangLang"
            package_root.mkdir(parents=True)
            source_exe = package_root / "LangLang.exe"
            source_exe.write_bytes(b"new-invalid-executable")
            (package_root / "update_manifest.json").write_text(
                json.dumps(
                    {
                        "version": "9.9.9",
                        "notes": "中文更新说明必须正确读取",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            staged = StagedUpdate(
                version="9.9.9",
                archive_path=root / "package.zip",
                staging_root=root / "stage",
                package_root=package_root,
            )
            with mock.patch.object(
                updater_module.subprocess,
                "Popen",
                return_value=object(),
            ), mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                log_path = launch_update_installer(staged, project)

            script_path = log_path.parent / "install-update.ps1"
            self.assertIn(
                "_internal\\assets\\auth_config.json",
                script_path.read_text(encoding="utf-8-sig"),
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    "-WaitPid",
                    "999999",
                    "-Source",
                    str(package_root),
                    "-Target",
                    str(project),
                    "-LogPath",
                    str(log_path),
                    "-ExpectedVersion",
                    "9.9.9",
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target_exe.read_bytes(), b"old-version")
            self.assertEqual(
                preserved_log.read_text(encoding="utf-8"),
                "pre-update log\n",
            )
            log_text = log_path.read_text(encoding="utf-8-sig")
            self.assertIn("Update failed", log_text)
            self.assertIn("Previous version restored", log_text)

    @unittest.skipUnless(os.name == "nt", "Windows installer test")
    def test_installer_keeps_sidecar_auth_without_copying_to_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "program" / "LangLang"
            project.mkdir(parents=True)
            target_exe = project / "LangLang.exe"
            target_exe.write_bytes(b"old-version")
            sidecar_auth = project / "auth_config.json"
            sidecar_auth.write_text('{"source":"sidecar"}', encoding="utf-8")
            internal_assets = project / "_internal" / "assets"
            internal_assets.mkdir(parents=True)
            (internal_assets / "auth_config.json").write_text(
                '{"source":"internal"}',
                encoding="utf-8",
            )

            package_root = root / "stage" / "LangLang"
            package_root.mkdir(parents=True)
            (package_root / "LangLang.exe").write_bytes(
                b"new-invalid-executable"
            )
            (package_root / "update_manifest.json").write_text(
                json.dumps({"version": "9.9.10"}),
                encoding="utf-8",
            )
            staged = StagedUpdate(
                version="9.9.10",
                archive_path=root / "package.zip",
                staging_root=root / "stage",
                package_root=package_root,
            )
            with mock.patch.object(
                updater_module.subprocess,
                "Popen",
                return_value=object(),
            ), mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root / "local")},
            ):
                log_path = launch_update_installer(staged, project)

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(log_path.parent / "install-update.ps1"),
                    "-WaitPid",
                    "999999",
                    "-Source",
                    str(package_root),
                    "-Target",
                    str(project),
                    "-LogPath",
                    str(log_path),
                    "-ExpectedVersion",
                    "9.9.10",
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            log_text = log_path.read_text(encoding="utf-8-sig")
            self.assertIn("Rollback copy:", log_text)
            self.assertIn("Previous version restored", log_text)
            self.assertEqual(target_exe.read_bytes(), b"old-version")
            self.assertEqual(
                sidecar_auth.read_text(encoding="utf-8"),
                '{"source":"sidecar"}',
            )


if __name__ == "__main__":
    unittest.main()
