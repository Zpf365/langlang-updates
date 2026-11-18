from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from weimi_manager.auth_client import AuthError
from weimi_manager.config import account_data_directory
from weimi_manager.tester_auth import TesterAuthService
from weimi_manager.ui.auth_window import AuthWindow


class TesterRemoteDelegationTests(unittest.TestCase):
    def _service(self, root: Path, data_dir: Path, remote: Mock) -> TesterAuthService:
        assets = root / "assets"
        assets.mkdir(parents=True)
        salt = "00" * 16
        password_hash = TesterAuthService._hash("local-password", salt)
        (assets / "universal_login.json").write_text(
            (
                '{"enabled": true, "accounts": [{'
                '"username": "local-user", '
                '"account_id": "tester-local", '
                f'"salt": "{salt}", '
                f'"password_hash": "{password_hash}"'
                "}]}"
            ),
            encoding="utf-8",
        )
        return TesterAuthService(root, data_dir, remote)

    def test_remote_registration_methods_are_available_on_hybrid_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resources"
            remote = Mock()
            remote.send_registration_code.return_value = None
            service = self._service(root, Path(tmp) / "data", remote)

            service.send_registration_code("user@example.com")

            remote.send_registration_code.assert_called_once_with("user@example.com")

    def test_non_local_login_falls_back_to_remote_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resources"
            remote = Mock()
            remote.login.return_value = "remote-context"
            service = self._service(root, Path(tmp) / "data", remote)

            result = service.login("remote-user", "password123", True)

            self.assertEqual(result, "remote-context")
            remote.login.assert_called_once_with("remote-user", "password123", True)

    def test_local_test_account_is_checked_after_remote_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resources"
            remote = Mock()
            remote.login.side_effect = AuthError(
                "账号不存在",
                404,
                "ACCOUNT_NOT_FOUND",
            )
            service = self._service(root, Path(tmp) / "data", remote)

            context = service.login("local-user", "local-password", False)

            self.assertEqual(context.account_id, "tester-local")
            remote.login.assert_called_once_with(
                "local-user",
                "local-password",
                False,
            )

    def test_registered_remote_account_wins_over_same_name_local_account(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resources"
            remote = Mock()
            remote.login.return_value = "remote-context"
            service = self._service(root, Path(tmp) / "data", remote)

            result = service.login("local-user", "remote-password", False)

            self.assertEqual(result, "remote-context")

    def test_remote_password_error_does_not_fall_back_to_local_account(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resources"
            remote = Mock()
            remote.login.side_effect = AuthError(
                "账户名或密码不正确",
                401,
                "INVALID_CREDENTIALS",
            )
            service = self._service(root, Path(tmp) / "data", remote)

            with self.assertRaises(AuthError) as raised:
                service.login("local-user", "wrong-password", False)

            self.assertEqual(raised.exception.code, "INVALID_CREDENTIALS")

    def test_remote_network_error_does_not_fall_back_to_local_account(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resources"
            remote = Mock()
            remote.login.side_effect = AuthError(
                "无法连接账号服务器",
                0,
            )
            service = self._service(root, Path(tmp) / "data", remote)

            with self.assertRaises(AuthError):
                service.login("local-user", "local-password", False)

    def test_tester_and_universal_account_files_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resources"
            remote = Mock()
            remote.login.side_effect = AuthError(
                "账号不存在",
                404,
                "ACCOUNT_NOT_FOUND",
            )
            self._service(root, Path(tmp) / "data", remote)
            assets = root / "assets"
            salt = "11" * 16
            password_hash = TesterAuthService._hash("legacy-password", salt)
            (assets / "tester_accounts.json").write_text(
                (
                    '{"enabled": true, "accounts": [{'
                    '"username": "qweasd", '
                    '"account_id": "tester-qweasd", '
                    f'"salt": "{salt}", '
                    f'"password_hash": "{password_hash}"'
                    "}]}"
                ),
                encoding="utf-8",
            )

            merged = TesterAuthService(root, Path(tmp) / "data", remote)

            self.assertIn("local-user", merged.accounts)
            self.assertIn("qweasd", merged.accounts)
            self.assertEqual(
                merged.login("qweasd", "legacy-password", False).account_id,
                "tester-qweasd",
            )


class TesterAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        path = Path(__file__).resolve().parent.parent / "assets" / "tester_accounts.json"
        if not path.is_file():
            raise unittest.SkipTest("发行安全基线不包含测试凭据")

    def test_all_twenty_test_accounts_use_unified_password(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            service = TesterAuthService(root, Path(tmp))
            self.assertTrue(service.enabled)
            for index in range(1, 21):
                context = service.login(
                    f"zpf{index}",
                    "z12345678",
                    False,
                )
                self.assertEqual(context.username, f"zpf{index}")
                self.assertEqual(context.account_id, f"tester-zpf{index}")
                self.assertTrue(context.license.active)
                self.assertTrue(context.license.permanent)
            qweasd = service.login("qweasd", "z12345678", False)
            self.assertEqual(qweasd.account_id, "tester-qweasd")
            self.assertTrue(qweasd.license.permanent)
            self.assertEqual(qweasd.username, "qweasd")
            self.assertNotIn("zpfly", service.accounts)

    def test_tester_account_data_paths_remain_compatible_with_0_6(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "LangLang" / "data"
            service = TesterAuthService(root, base)
            expected_directories = {
                "zpf1": "682bb21145c0805951ea10e8",
                "zpf20": "32aa9cccfc5389d46c584d95",
                "qweasd": "f9c7865306198d5a21707162",
            }
            for username, expected_directory in expected_directories.items():
                context = service.login(username, "z12345678", False)
                path = account_data_directory(base, context.account_id)
                self.assertEqual(path.parent.name, expected_directory)

    def test_tester_login_can_save_password_for_next_start(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "LangLang" / "data"
            service = TesterAuthService(root, base)

            service.login("qweasd", "z12345678", True)

            saved = service.store.list_accounts()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].username, "qweasd")
            self.assertEqual(saved[0].password, "z12345678")

    def test_wrong_tester_password_is_rejected(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            service = TesterAuthService(root, Path(tmp))
            with self.assertRaises(AuthError):
                service.login("zpf1", "wrong-password", False)

    def test_auth_window_accepts_zpf1_and_enters_directly(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "LangLang" / "data"
            service = TesterAuthService(root, base)
            window = AuthWindow(
                type("Paths", (), {"resource_dir": root})(),
                service,
            )
            try:
                window.login_account.insert(0, "zpf1")
                window.login_password.insert(0, "z12345678")
                window._login()
                deadline = time.monotonic() + 3
                while window.result is None and time.monotonic() < deadline:
                    try:
                        window.update()
                    except Exception:
                        break
                self.assertIsNotNone(window.result)
                self.assertEqual(window.result.username, "zpf1")
            finally:
                window._close()


if __name__ == "__main__":
    unittest.main()
