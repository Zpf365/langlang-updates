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
    StagedUpdate,
    UpdateInfo,
    _download_archive,
    _get_with_retries,
    _requests_session,
    check_for_update,
    download_and_stage_update,
    is_development_preview_version,
    launch_update_installer,
)


class UpdaterTests(unittest.TestCase):
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

    def test_stage_update_requires_expected_version_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "dist" / "LangLang"
            project.mkdir(parents=True)
            package = root / "LangLang_9.0.1.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("LangLang/LangLang.exe", b"exe")
                archive.writestr(
                    "LangLang/update_manifest.json",
                    json.dumps({"version": "9.0.1"}),
                )
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
            self.assertIn("move new version into target", script)
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
            log_text = log_path.read_text(encoding="utf-8-sig")
            self.assertIn("Update failed", log_text)
            self.assertIn("Previous version restored", log_text)


if __name__ == "__main__":
    unittest.main()
