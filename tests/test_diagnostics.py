from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from weimi_manager.diagnostics import DiagnosticManager, redact_text

ROOT = Path(__file__).resolve().parent.parent


class DiagnosticTests(unittest.TestCase):
    def test_sensitive_values_are_redacted(self) -> None:
        text = (
            "cookie=abc token:xyz phone=13812345678 "
            "email=user@example.com"
        )
        result = redact_text(text)
        self.assertNotIn("abc", result)
        self.assertNotIn("xyz", result)
        self.assertNotIn("13812345678", result)
        self.assertNotIn("user@example.com", result)

    def test_reports_are_queued_locally_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(
                Path(tmp),
                "0.6.53",
                account_id="account-1",
                username="zpf1",
            )
            manager.context_provider = lambda: {
                "route": "/products/upload",
                "page": "upload",
                "task": {"task_id": "task-1", "status": "running"},
            }
            try:
                path = manager.record(
                    "ui_freeze",
                    "main thread delayed 4200ms",
                    severity="warning",
                    detail={"phone": "13812345678"},
                )
                report = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(report["username"], "zpf1")
                self.assertEqual(report["route"], "/products/upload")
                self.assertEqual(report["detail"]["phone"], "<REDACTED>")
                self.assertEqual(manager.pending_count(), 1)
            finally:
                manager.close()

    def test_overflow_keeps_latest_200_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(Path(tmp), "0.6.53")
            try:
                for index in range(205):
                    manager.record("event", str(index))
                self.assertEqual(manager.pending_count(), 200)
            finally:
                manager.close()

    def test_non_info_report_starts_upload_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(
                Path(tmp),
                "0.6.57",
                upload_url="https://example.test/diagnostics",
                tester_key="tester-key",
            )
            try:
                with mock.patch.object(manager, "flush_async") as flush_async:
                    manager.record(
                        "ui_freeze",
                        "main thread delayed 4200ms",
                        severity="warning",
                    )
                flush_async.assert_called_once_with()
                self.assertEqual(manager.pending_count(), 1)
            finally:
                manager.close()

    def test_info_feedback_does_not_start_automatic_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(
                Path(tmp),
                "0.6.57",
                upload_url="https://example.test/diagnostics",
                tester_key="tester-key",
            )
            try:
                with mock.patch.object(manager, "flush_async") as flush_async:
                    manager.record(
                        "tester_feedback",
                        "可选的补充说明",
                        severity="info",
                    )
                flush_async.assert_not_called()
                self.assertEqual(manager.pending_count(), 1)
            finally:
                manager.close()

    def test_upload_requires_both_receiver_and_credential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(Path(tmp), "0.6.57")
            try:
                self.assertFalse(manager.upload_ready)
                with mock.patch(
                    "weimi_manager.diagnostics.threading.Thread"
                ) as thread:
                    manager.flush_async()
                thread.assert_not_called()
                manager.upload_url = "https://example.test/diagnostics"
                self.assertFalse(manager.upload_ready)
                manager.tester_key = "tester-key"
                self.assertTrue(manager.upload_ready)
            finally:
                manager.close()

    def test_successful_upload_removes_report_from_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(
                Path(tmp),
                "0.6.53",
                upload_url="https://example.test/diagnostics",
                access_token="token",
                auto_upload=False,
            )
            try:
                manager.record("event", "upload me")
                response = mock.Mock()
                response.raise_for_status.return_value = None
                with mock.patch(
                    "weimi_manager.diagnostics.requests.post",
                    return_value=response,
                ) as post:
                    self.assertEqual(manager.flush(), 1)
                post.assert_called_once()
                self.assertEqual(manager.pending_count(), 0)
            finally:
                manager.close()

    def test_expired_upload_token_refreshes_once_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current_tokens = {"access": "expired-token"}
            refresh_calls: list[str] = []

            def refresh(failed_token: str) -> str:
                refresh_calls.append(failed_token)
                current_tokens["access"] = "fresh-token"
                return current_tokens["access"]

            manager = DiagnosticManager(
                Path(tmp),
                "1.8.49",
                upload_url="https://example.test/diagnostics",
                access_token_provider=lambda: current_tokens["access"],
                access_token_refresher=refresh,
                auto_upload=False,
            )
            try:
                manager.record("event", "upload after refresh")
                expired = mock.Mock(status_code=401)
                success = mock.Mock(status_code=200)
                success.raise_for_status.return_value = None
                with mock.patch(
                    "weimi_manager.diagnostics.requests.post",
                    side_effect=[expired, success],
                ) as post:
                    self.assertEqual(manager.flush(), 1)
                self.assertEqual(refresh_calls, ["expired-token"])
                self.assertEqual(post.call_count, 2)
                self.assertEqual(
                    post.call_args_list[0].kwargs["headers"]["Authorization"],
                    "Bearer expired-token",
                )
                self.assertEqual(
                    post.call_args_list[1].kwargs["headers"]["Authorization"],
                    "Bearer fresh-token",
                )
                self.assertEqual(manager.pending_count(), 0)
            finally:
                manager.close()

    def test_upload_failure_keeps_report_for_later_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(
                Path(tmp),
                "1.8.49",
                upload_url="https://example.test/diagnostics",
                access_token="token",
                auto_upload=False,
            )
            try:
                manager.record("event", "must stay queued")
                denied = mock.Mock(status_code=401)
                with mock.patch(
                    "weimi_manager.diagnostics.requests.post",
                    return_value=denied,
                ):
                    self.assertEqual(manager.flush(), 0)
                self.assertEqual(manager.pending_count(), 1)
            finally:
                manager.close()

    def test_diagnostic_upload_uses_current_session_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = {"token": "first"}
            manager = DiagnosticManager(
                Path(tmp),
                "1.8.49",
                upload_url="https://example.test/diagnostics",
                access_token_provider=lambda: current["token"],
                auto_upload=False,
            )
            try:
                manager.record("event", "first")
                first = mock.Mock(status_code=200)
                first.raise_for_status.return_value = None
                with mock.patch(
                    "weimi_manager.diagnostics.requests.post",
                    return_value=first,
                ) as post:
                    current["token"] = "current"
                    self.assertEqual(manager.flush(), 1)
                self.assertEqual(
                    post.call_args.kwargs["headers"]["Authorization"],
                    "Bearer current",
                )
            finally:
                manager.close()

    def test_diagnostic_reports_service_role_migration_grants_rw(self) -> None:
        migration = (
            ROOT
            / "supabase"
            / "migrations"
            / "202611190002_diagnostic_reports_service_role.sql"
        )
        self.assertTrue(migration.is_file(), migration)
        sql = migration.read_text(encoding="utf-8")
        self.assertIn("grant select, insert, update", sql)
        self.assertIn("public.diagnostic_reports", sql)
        self.assertIn("to service_role", sql)

    def test_unclean_previous_session_is_reported_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            diagnostics_dir = Path(tmp) / "diagnostics"
            diagnostics_dir.mkdir(parents=True)
            (diagnostics_dir / "runtime_state.json").write_text(
                json.dumps(
                    {
                        "session_id": "previous",
                        "pid": 999999,
                        "version": "1.7.5",
                        "started_at": datetime.now(UTC).isoformat(),
                        "clean_exit": False,
                    }
                ),
                encoding="utf-8",
            )
            manager = DiagnosticManager(
                Path(tmp),
                "1.7.5",
                stall_threshold=30,
            )
            try:
                reports = manager.recent()
            finally:
                manager.close()
        categories = {report["category"] for report in reports}
        self.assertIn("previous_session_crash", categories)
        crash = next(
            report
            for report in reports
            if report["category"] == "previous_session_crash"
        )
        self.assertEqual(crash["severity"], "fatal")
        self.assertEqual(
            crash["detail"]["previous_session"]["session_id"],
            "previous",
        )

    def test_ui_stall_report_contains_main_thread_stack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(
                Path(tmp),
                "1.7.5",
                stall_threshold=0.5,
                stall_check_interval=60,
            )
            try:
                manager._last_heartbeat_monotonic = time.monotonic() - 2
                manager.check_ui_stall()
                reports = manager.recent()
            finally:
                manager.close()
        stall = next(
            report for report in reports if report["category"] == "ui_stall"
        )
        self.assertEqual(stall["severity"], "error")
        self.assertGreaterEqual(stall["detail"]["stall_seconds"], 0.5)
        self.assertIn("check_ui_stall", stall["detail"]["main_thread_stack"])

    def test_exception_report_includes_root_cause_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = DiagnosticManager(
                Path(tmp),
                "1.7.5",
                stall_threshold=30,
            )
            try:
                try:
                    try:
                        raise KeyError("source record missing")
                    except KeyError as exc:
                        raise ValueError("upload failed") from exc
                except ValueError as exc:
                    path = manager.capture_exception(exc)
                report = json.loads(path.read_text(encoding="utf-8"))
            finally:
                manager.close()
        self.assertEqual(report["detail"]["exception_type"], "ValueError")
        self.assertEqual(report["detail"]["exception_chain"][0], "ValueError: upload failed")
        self.assertIn(
            "KeyError: 'source record missing'",
            report["detail"]["root_cause"],
        )

    def test_clean_exit_does_not_emit_false_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = DiagnosticManager(
                Path(tmp),
                "1.7.5",
                stall_threshold=30,
            )
            first.close()
            second = DiagnosticManager(
                Path(tmp),
                "1.7.5",
                stall_threshold=30,
            )
            try:
                reports = second.recent()
            finally:
                second.close()
        self.assertFalse(
            any(
                report["category"] == "previous_session_crash"
                for report in reports
            )
        )


if __name__ == "__main__":
    unittest.main()
