from __future__ import annotations

import sqlite3
import tempfile
import tkinter as tk
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests

from weimi_manager.account_store_sync import (
    VAULT_METADATA_KEY,
    decrypt_store_snapshot,
    derive_vault_key,
    encrypt_store_snapshot,
)
from weimi_manager.auth_client import (
    AuthError,
    LicenseStatus,
    SessionTokens,
    SupabaseAuthClient,
)
from weimi_manager.auth_config import AuthConfig
from weimi_manager.auth_service import AuthContext, AuthService
from weimi_manager.auth_session import AuthSessionStore, SavedAccount
from weimi_manager.config import (
    _database_store_count,
    migrate_legacy_data_to_account,
)
from weimi_manager.time_guard import NetworkTimeGuard
from weimi_manager.ui.auth_window import (
    INPUT_TEXT,
    AuthWindow,
    license_allows_entry,
)


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class _ProxyTimeoutSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        raise requests.ReadTimeout("proxy handshake timed out")


def _create_store_database(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                app_id TEXT NOT NULL UNIQUE,
                secret_protected BLOB NOT NULL
            )
            """
        )
        for index, name in enumerate(names, 1):
            conn.execute(
                "INSERT INTO stores (name, app_id, secret_protected) VALUES (?, ?, ?)",
                (name, f"wx-{index}", b"secret"),
            )
        conn.commit()
    finally:
        conn.close()


class AuthConfigTests(unittest.TestCase):
    def test_environment_configuration_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            data = root / "LangLang" / "data"
            project.mkdir()
            data.mkdir(parents=True)
            paths = mock.Mock(
                project_dir=project,
                resource_dir=project,
                data_dir=data,
            )
            with mock.patch.dict(
                "os.environ",
                {
                    "LANGLANG_SUPABASE_URL": "https://demo.supabase.co",
                    "LANGLANG_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_demo",
                },
                clear=False,
            ):
                config = AuthConfig.load(paths)
            self.assertTrue(config.configured)
            self.assertEqual(
                config.function_url("license-status"),
                "https://demo.supabase.co/functions/v1/license-status",
            )


class AuthSessionTests(unittest.TestCase):
    def test_saved_sessions_round_trip_without_plaintext_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthSessionStore(Path(tmp))
            account = SavedAccount(
                account_id="user-1",
                username="qqwww",
                email="qqwww@example.com",
                access_token="access-value",
                refresh_token="refresh-value",
                expires_at="2026-11-08T12:00:00+00:00",
                last_login_at="2026-11-08T10:00:00+00:00",
            )
            store.save(account)
            raw = store.path.read_bytes()
            self.assertNotIn(b"refresh-value", raw)
            loaded = store.list_accounts()
            self.assertEqual(loaded, [account])
            store.remove(account.account_id)
            self.assertFalse(store.list_accounts())

    def test_saved_password_is_encrypted_and_available_for_prefill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthSessionStore(Path(tmp))
            account = SavedAccount(
                account_id="user-password",
                username="zpfly",
                email="zpfly@example.com",
                access_token="",
                refresh_token="",
                expires_at="",
                last_login_at="2026-11-10T00:00:00+00:00",
                password="z12345678",
            )

            store.save(account)

            self.assertNotIn(b"z12345678", store.path.read_bytes())
            self.assertEqual(
                store.list_accounts()[0].password,
                "z12345678",
            )

    def test_saving_same_username_replaces_previous_account_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthSessionStore(Path(tmp))
            common = {
                "username": "qweasd",
                "email": "qweasd@example.com",
                "access_token": "",
                "refresh_token": "",
                "expires_at": "",
                "last_login_at": "2026-11-10T00:00:00+00:00",
            }
            store.save(
                SavedAccount(
                    account_id="cloud-qweasd",
                    **common,
                )
            )
            store.save(
                SavedAccount(
                    account_id="tester-qweasd",
                    password="z12345678",
                    **common,
                )
            )

            accounts = store.list_accounts()
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0].account_id, "tester-qweasd")

    def test_offline_grace_is_limited_to_one_hour(self) -> None:
        class OfflineClient:
            def refresh(self, _token):
                raise AuthError("offline", 0)

        with tempfile.TemporaryDirectory() as tmp:
            store = AuthSessionStore(Path(tmp))
            service = AuthService(OfflineClient(), store)
            now = datetime.now(UTC)
            account = SavedAccount(
                account_id="user-1",
                username="qqwww",
                email="qqwww@example.com",
                access_token="access",
                refresh_token="refresh",
                expires_at=now.isoformat(),
                last_login_at=now.isoformat(),
                license_active=True,
                license_expires_at=(now + timedelta(days=2)).isoformat(),
                last_server_time=now.isoformat(),
                last_verified_local_time=now.isoformat(),
            )
            context = service.restore_account(account)
            self.assertTrue(context.offline)
            self.assertTrue(context.license.active)

            expired = SavedAccount(
                **{
                    **account.__dict__,
                    "last_verified_local_time": (now - timedelta(hours=2)).isoformat(),
                }
            )
            with self.assertRaises(AuthError):
                service.restore_account(expired)

    def test_password_login_prepares_portable_store_vault_key(self) -> None:
        class LoginClient:
            def login_by_username(self, username, _password):
                return SessionTokens(
                    user_id="user-1",
                    account_id="account-1",
                    username=username,
                    email="user@example.com",
                    access_token="access",
                    refresh_token="refresh",
                    expires_at="2026-11-08T12:00:00+00:00",
                    metadata={"username": username},
                )

            def register_device(self, *_args):
                return {}

            def license_status(self, _tokens):
                return LicenseStatus(
                    active=True,
                    permanent=True,
                    expires_at=None,
                    status_text="永久授权",
                    remaining_days=9999,
                    max_devices=1,
                    device_bound=True,
                    server_time="2026-11-08T10:00:00+00:00",
                )

        with tempfile.TemporaryDirectory() as tmp:
            service = AuthService(
                LoginClient(),
                AuthSessionStore(Path(tmp)),
            )
            context = service.login("qqwww", "password123", True)
            saved_password = service.store.list_accounts()[0].password
        self.assertEqual(
            context.vault_key,
            derive_vault_key("password123", "account-1"),
        )
        self.assertEqual(saved_password, "password123")
        self.assertNotIn(
            "password123",
            repr(context),
        )

    def test_password_change_reencrypts_store_vault(self) -> None:
        account_id = "account-password"
        snapshot = {
            "type": "langlang-store-accounts",
            "version": 1,
            "groups": [],
            "stores": [{"app_id": "wx-one", "name": "店铺一"}],
        }
        old_envelope = encrypt_store_snapshot(
            snapshot,
            derive_vault_key("old-password", account_id),
            account_id,
        )

        class ChangePasswordClient:
            def __init__(self) -> None:
                self.updated_metadata = None
                self.updated_password = ""

            def login_by_username(self, username, password):
                if password != "old-password":
                    raise AuthError("密码错误", 401)
                return SessionTokens(
                    user_id=account_id,
                    account_id=account_id,
                    username=username,
                    email="user@example.com",
                    access_token="access",
                    refresh_token="refresh",
                    expires_at="2026-11-11T12:00:00+00:00",
                    metadata={VAULT_METADATA_KEY: old_envelope},
                )

            def update_password(self, _access_token, password):
                self.updated_password = password

            def update_user_metadata(self, _access_token, metadata):
                self.updated_metadata = metadata
                return {}

        client = ChangePasswordClient()
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthSessionStore(Path(tmp))
            service = AuthService(client, store)
            service.change_password(
                "zpfly",
                "old-password",
                "new-password",
            )
            saved_password = store.list_accounts()[0].password
        self.assertEqual(client.updated_password, "new-password")
        self.assertEqual(saved_password, "new-password")
        new_envelope = client.updated_metadata[VAULT_METADATA_KEY]
        self.assertEqual(
            decrypt_store_snapshot(
                new_envelope,
                derive_vault_key("new-password", account_id),
                account_id,
            ),
            snapshot,
        )

    def test_login_attaches_organization_role_and_permissions(self) -> None:
        class OrganizationClient:
            def login_by_username(self, username, _password):
                return SessionTokens(
                    user_id="user-org",
                    account_id="user-org",
                    username=username,
                    email="org@example.com",
                    access_token="access",
                    refresh_token="refresh",
                    expires_at="2026-11-11T12:00:00+00:00",
                )

            def organization_context(self, _access_token):
                return {
                    "organization_id": "org-1",
                    "organization_name": "ZPF 团队",
                    "member_id": "member-1",
                    "role": "operator",
                    "permissions": {
                        "stores_view": True,
                        "products_upload": True,
                    },
                }

            def register_device(self, *_args):
                return {}

            def license_status(self, _tokens):
                return LicenseStatus(
                    active=True,
                    permanent=True,
                    expires_at=None,
                    status_text="组织授权",
                    remaining_days=999999,
                    max_devices=10,
                    device_bound=True,
                    server_time="2026-11-11T10:00:00+00:00",
                )

        with tempfile.TemporaryDirectory() as tmp:
            service = AuthService(
                OrganizationClient(),
                AuthSessionStore(Path(tmp)),
            )
            context = service.login("ZPF:01", "password123", False)
        self.assertEqual(context.organization_id, "org-1")
        self.assertEqual(context.member_role, "operator")
        self.assertTrue(context.permissions["stores_view"])

    def test_login_fails_closed_when_organization_check_fails(self) -> None:
        class OrganizationFailureClient:
            def login_by_username(self, username, _password):
                return SessionTokens(
                    user_id="user-org",
                    account_id="user-org",
                    username=username,
                    email="org@example.com",
                    access_token="access",
                    refresh_token="refresh",
                    expires_at="2026-11-11T12:00:00+00:00",
                )

            def organization_context(self, _access_token):
                raise AuthError("network down", 0)

            def register_device(self, *_args):
                raise AssertionError("device registration must not run")

        with tempfile.TemporaryDirectory() as tmp:
            service = AuthService(
                OrganizationFailureClient(),
                AuthSessionStore(Path(tmp)),
            )
            with self.assertRaisesRegex(AuthError, "团队权限校验失败"):
                service.login("ZPF:01", "password123", False)

    def test_subaccount_cannot_change_own_password(self) -> None:
        class SubaccountClient:
            def login_by_username(self, username, _password):
                return SessionTokens(
                    user_id="sub-user",
                    account_id="sub-user",
                    username=username,
                    email="sub@example.com",
                    access_token="access",
                    refresh_token="refresh",
                    expires_at="2026-11-11T12:00:00+00:00",
                )

            def organization_context(self, _access_token):
                return {"role": "operator", "permissions": {}}

            def update_password(self, *_args):
                raise AssertionError("subaccount password update must not run")

        with tempfile.TemporaryDirectory() as tmp:
            service = AuthService(
                SubaccountClient(),
                AuthSessionStore(Path(tmp)),
            )
            with self.assertRaises(AuthError):
                service.change_password(
                    "ZPF:01",
                    "old-password",
                    "new-password",
                )


class AuthClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AuthConfig(
            supabase_url="https://demo.supabase.co",
            publishable_key="sb_publishable_demo",
        )

    def test_registration_reserves_username_then_signs_up(self) -> None:
        fake = _Session(
            [
                _Response(200, True),
                _Response(
                    200,
                    {
                        "user": {
                            "id": "user-1",
                            "email": "qqwww@example.com",
                            "user_metadata": {"username": "qqwww"},
                        }
                    },
                ),
            ]
        )
        client = SupabaseAuthClient(self.config, fake)
        client.sign_up("qqwww@example.com", "password123", "qqwww")
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(fake.calls[0][1].endswith("/rest/v1/rpc/check_username_available"))
        self.assertTrue(fake.calls[1][1].endswith("/auth/v1/signup"))
        self.assertEqual(
            fake.calls[1][2]["json"]["data"]["username"],
            "qqwww",
        )

    def test_email_otp_can_be_sent_before_account_fields(self) -> None:
        fake = _Session(
            [
                _Response(200, {}),
                _Response(
                    200,
                    {
                        "access_token": "access",
                        "refresh_token": "refresh",
                        "expires_in": 3600,
                        "user": {
                            "id": "user-2",
                            "email": "otp@example.com",
                            "user_metadata": {},
                        },
                    },
                ),
            ]
        )
        client = SupabaseAuthClient(self.config, fake)
        client.send_signup_otp("otp@example.com")
        tokens = client.verify_email_otp(
            "otp@example.com",
            "123456",
            "otp-user",
        )
        self.assertIsNotNone(tokens)
        self.assertEqual(tokens.username, "otp-user")
        self.assertTrue(fake.calls[0][1].endswith("/auth/v1/otp"))
        self.assertEqual(
            fake.calls[0][2]["json"],
            {"email": "otp@example.com", "create_user": True},
        )
        self.assertTrue(fake.calls[1][1].endswith("/auth/v1/verify"))
        self.assertEqual(fake.calls[1][2]["json"]["type"], "email")

    def test_username_login_uses_secure_edge_function(self) -> None:
        fake = _Session(
            [
                _Response(
                    200,
                    {
                        "access_token": "access",
                        "refresh_token": "refresh",
                        "expires_in": 3600,
                        "user": {
                            "id": "user-1",
                            "email": "qqwww@example.com",
                            "user_metadata": {"username": "qqwww"},
                        },
                    },
                ),
            ]
        )
        client = SupabaseAuthClient(self.config, fake)
        tokens = client.login_by_username("qqwww", "password123")
        self.assertEqual(tokens.username, "qqwww")
        self.assertEqual(tokens.refresh_token, "refresh")
        self.assertTrue(
            fake.calls[0][1].endswith("/functions/v1/login-by-username")
        )
        self.assertEqual(
            fake.calls[0][2]["json"],
            {"username": "qqwww", "password": "password123"},
        )

    def test_username_login_preserves_account_not_found_code(self) -> None:
        fake = _Session(
            [
                _Response(
                    404,
                    {
                        "error": "账户名不存在",
                        "code": "ACCOUNT_NOT_FOUND",
                    },
                )
            ]
        )
        client = SupabaseAuthClient(self.config, fake)
        with self.assertRaises(AuthError) as raised:
            client.login_by_username("missing-user", "password123")
        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "ACCOUNT_NOT_FOUND")

    def test_username_login_retries_direct_when_system_proxy_times_out(
        self,
    ) -> None:
        proxy = _ProxyTimeoutSession()
        direct = _Session(
            [
                _Response(
                    200,
                    {
                        "access_token": "access",
                        "refresh_token": "refresh",
                        "expires_in": 3600,
                        "user": {
                            "id": "user-1",
                            "email": "qqwww@example.com",
                            "user_metadata": {"username": "qqwww"},
                        },
                    },
                )
            ]
        )
        client = SupabaseAuthClient(
            self.config,
            proxy,
            direct_session=direct,
        )
        with mock.patch(
            "weimi_manager.auth_client.requests.utils.get_environ_proxies",
            return_value={"https": "http://127.0.0.1:10809"},
        ):
            tokens = client.login_by_username("qqwww", "password123")

        self.assertEqual(tokens.refresh_token, "refresh")
        self.assertEqual(len(proxy.calls), 1)
        self.assertEqual(len(direct.calls), 1)
        self.assertTrue(
            direct.calls[0][1].endswith("/functions/v1/login-by-username")
        )

    def test_update_user_metadata_uses_authenticated_user_endpoint(self) -> None:
        fake = _Session(
            [
                _Response(
                    200,
                    {
                        "id": "user-1",
                        "user_metadata": {"vault": {"version": 1}},
                    },
                )
            ]
        )
        client = SupabaseAuthClient(self.config, fake)
        client.update_user_metadata(
            "access-token",
            {"vault": {"version": 1}},
        )
        method, url, kwargs = fake.calls[0]
        self.assertEqual(method, "PUT")
        self.assertTrue(url.endswith("/auth/v1/user"))
        self.assertEqual(
            kwargs["json"],
            {"data": {"vault": {"version": 1}}},
        )
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer access-token",
        )

    def test_expired_jwt_refreshes_session_and_retries_once(self) -> None:
        fake = _Session(
            [
                _Response(401, {"message": "JWT expired"}),
                _Response(
                    200,
                    {
                        "access_token": "fresh-access",
                        "refresh_token": "fresh-refresh",
                        "expires_in": 3600,
                        "user": {
                            "id": "user-1",
                            "email": "qqwww@example.com",
                            "user_metadata": {"username": "qqwww"},
                        },
                    },
                ),
                _Response(
                    200,
                    {
                        "request_id": "request-1",
                        "authorized": True,
                        "command": "stores.view",
                        "store_app_id": "",
                    },
                ),
                _Response(
                    200,
                    {
                        "request_id": "request-2",
                        "authorized": True,
                        "command": "stores.view",
                        "store_app_id": "",
                    },
                ),
            ]
        )
        refreshed: list[SessionTokens] = []
        client = SupabaseAuthClient(self.config, fake)
        client.bind_session(
            SessionTokens(
                user_id="user-1",
                account_id="user-1",
                username="qqwww",
                email="qqwww@example.com",
                access_token="expired-access",
                refresh_token="refresh",
                expires_at="2026-11-13T00:00:00+00:00",
            ),
            refreshed.append,
        )

        result = client.authorize_organization_command(
            "expired-access",
            "request-1",
            "stores.view",
        )

        self.assertTrue(result["authorized"])
        self.assertEqual(len(fake.calls), 3)
        self.assertTrue(
            fake.calls[1][1].endswith(
                "/auth/v1/token?grant_type=refresh_token"
            )
        )
        self.assertEqual(
            fake.calls[2][2]["headers"]["Authorization"],
            "Bearer fresh-access",
        )
        self.assertEqual(refreshed[0].access_token, "fresh-access")
        self.assertEqual(refreshed[0].refresh_token, "fresh-refresh")

        client.authorize_organization_command(
            "expired-access",
            "request-2",
            "stores.view",
        )

        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(
            fake.calls[3][2]["headers"]["Authorization"],
            "Bearer fresh-access",
        )

    def test_refreshed_session_is_persisted_without_losing_license(self) -> None:
        class RefreshableClient:
            def __init__(self) -> None:
                self.callback = None

            def login_by_username(self, username, _password):
                return SessionTokens(
                    user_id="user-1",
                    account_id="user-1",
                    username=username,
                    email="qqwww@example.com",
                    access_token="old-access",
                    refresh_token="old-refresh",
                    expires_at="2026-11-13T00:00:00+00:00",
                )

            def bind_session(self, _tokens, callback):
                self.callback = callback

            def register_device(self, *_args):
                return {}

            def license_status(self, _tokens):
                return LicenseStatus(
                    active=True,
                    permanent=False,
                    expires_at="2026-12-13T00:00:00+00:00",
                    status_text="有效",
                    remaining_days=30,
                    max_devices=1,
                    device_bound=True,
                    server_time="2026-11-13T00:00:00+00:00",
                )

        with tempfile.TemporaryDirectory() as tmp:
            client = RefreshableClient()
            service = AuthService(
                client,
                AuthSessionStore(Path(tmp)),
            )
            service.login("qqwww", "password123", True)

            client.callback(
                SessionTokens(
                    user_id="user-1",
                    account_id="user-1",
                    username="qqwww",
                    email="qqwww@example.com",
                    access_token="new-access",
                    refresh_token="new-refresh",
                    expires_at="2026-11-14T00:00:00+00:00",
                )
            )
            saved = service.store.find("qqwww")

        self.assertIsNotNone(saved)
        self.assertEqual(saved.access_token, "new-access")
        self.assertEqual(saved.refresh_token, "new-refresh")
        self.assertTrue(saved.license_active)
        self.assertEqual(
            saved.license_expires_at,
            "2026-12-13T00:00:00+00:00",
        )

    def test_expired_refresh_token_returns_actionable_auth_error(self) -> None:
        fake = _Session(
            [
                _Response(401, {"message": "JWT expired"}),
                _Response(
                    400,
                    {"error": "refresh_token_not_found"},
                ),
            ]
        )
        client = SupabaseAuthClient(self.config, fake)
        client.bind_session(
            SessionTokens(
                user_id="user-1",
                account_id="user-1",
                username="qqwww",
                email="qqwww@example.com",
                access_token="expired-access",
                refresh_token="expired-refresh",
                expires_at="2026-11-13T00:00:00+00:00",
            )
        )

        with self.assertRaises(AuthError) as raised:
            client.authorize_organization_command(
                "expired-access",
                "request-1",
                "stores.view",
            )

        self.assertEqual(raised.exception.status, 401)
        self.assertEqual(raised.exception.code, "SESSION_EXPIRED")
        self.assertIn("请重新登录", str(raised.exception))


class AccountMigrationTests(unittest.TestCase):
    def test_first_account_copies_legacy_data_and_later_accounts_are_empty(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "LangLang" / "data"
            _create_store_database(base / "weimi_manager.db", ["A", "B"])
            first = migrate_legacy_data_to_account(base, "account-one")
            second = migrate_legacy_data_to_account(base, "account-two")
            self.assertEqual(
                _database_store_count(first / "weimi_manager.db"),
                2,
            )
            self.assertEqual(
                _database_store_count(second / "weimi_manager.db"),
                0,
            )
            self.assertEqual(
                _database_store_count(base / "weimi_manager.db"),
                2,
            )


class NetworkTimeGuardTests(unittest.TestCase):
    def test_uses_multiple_http_date_headers(self) -> None:
        dates = iter(
            (
                "Sun, 08 Nov 2026 10:00:00 GMT",
                "Sun, 08 Nov 2026 10:00:02 GMT",
                "Sun, 08 Nov 2026 10:00:01 GMT",
            )
        )

        class Response:
            def __init__(self):
                self.headers = {"Date": next(dates)}

            def close(self):
                return None

        with mock.patch(
            "weimi_manager.time_guard.requests.get",
            side_effect=lambda *_args, **_kwargs: Response(),
        ):
            sample = NetworkTimeGuard().sample()
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(
            sample.now.replace(microsecond=0),
            datetime(2026, 11, 8, 10, 0, 1, tzinfo=UTC),
        )


class AuthWindowTests(unittest.TestCase):
    def test_future_expiration_enters_without_card_page(self) -> None:
        future = LicenseStatus(
            active=False,
            permanent=False,
            expires_at=(datetime.now(UTC) + timedelta(days=5)).isoformat(),
            status_text="系统同步延迟",
            remaining_days=5,
            max_devices=1,
            device_bound=True,
            server_time=datetime.now(UTC).isoformat(),
        )
        expired = LicenseStatus(
            active=False,
            permanent=False,
            expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
            status_text="已到期",
            remaining_days=0,
            max_devices=1,
            device_bound=True,
            server_time=datetime.now(UTC).isoformat(),
        )

        self.assertTrue(license_allows_entry(future))
        self.assertFalse(license_allows_entry(expired))

    def test_expired_account_only_shows_card_redemption(self) -> None:
        now = datetime.now(UTC)
        tokens = SessionTokens(
            user_id="expired-user",
            account_id="expired-user",
            username="expired",
            email="expired@example.com",
            access_token="",
            refresh_token="",
            expires_at=now.isoformat(),
        )
        context = AuthContext(
            tokens=tokens,
            license=LicenseStatus(
                active=False,
                permanent=False,
                expires_at=(now - timedelta(days=1)).isoformat(),
                status_text="已到期",
                remaining_days=0,
                max_devices=1,
                device_bound=True,
                server_time=now.isoformat(),
            ),
            device_hash="d" * 64,
            device_name="test",
        )

        with tempfile.TemporaryDirectory() as tmp:
            service = SimpleNamespace(
                store=SimpleNamespace(list_accounts=lambda: []),
            )
            try:
                window = AuthWindow(
                    SimpleNamespace(resource_dir=Path(tmp)),
                    service,
                )
            except tk.TclError as exc:
                self.skipTest(str(exc))
            try:
                window._authorized(context)
                window.update_idletasks()
                self.assertEqual(window._screen, "license")
                self.assertEqual(
                    window.license_enter_button.winfo_manager(),
                    "",
                )
                self.assertEqual(window.license_code.cget("state"), "normal")
            finally:
                window._close()

    def test_saved_password_is_prefilled_and_masked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            account = SavedAccount(
                account_id="user-prefill",
                username="zpfly",
                email="zpfly@example.com",
                access_token="",
                refresh_token="",
                expires_at="",
                last_login_at="2026-11-10T00:00:00+00:00",
                password="z12345678",
            )
            service = SimpleNamespace(
                store=SimpleNamespace(
                    list_accounts=lambda: [account],
                )
            )
            paths = SimpleNamespace(resource_dir=Path(tmp))

            try:
                window = AuthWindow(paths, service)
            except tk.TclError as exc:
                self.skipTest(str(exc))
            try:
                self.assertEqual(window.login_account.get(), "zpfly")
                self.assertEqual(window.login_password.get(), "z12345678")
                self.assertEqual(window.login_password.cget("show"), "●")
            finally:
                window._close()

    def test_cloud_login_screen_builds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = SimpleNamespace(
                store=SimpleNamespace(list_accounts=lambda: []),
            )
            paths = SimpleNamespace(resource_dir=root)
            try:
                window = AuthWindow(paths, service)
            except tk.TclError as exc:
                self.skipTest(str(exc))
            try:
                window.update_idletasks()
                self.assertEqual(window._screen, "login")
                self.assertEqual(window.login_account.get(), "")
                self.assertEqual(
                    window.update_button.cget("text"),
                    "检查更新",
                )
            finally:
                window._close()

    def test_midnight_theme_keeps_input_text_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = SimpleNamespace(
                store=SimpleNamespace(list_accounts=lambda: []),
            )
            paths = SimpleNamespace(resource_dir=root)
            preferences = SimpleNamespace(theme="midnight_slate")
            try:
                with mock.patch(
                    "weimi_manager.ui.v2.theme.load_preferences",
                    return_value=preferences,
                ):
                    window = AuthWindow(paths, service)
            except tk.TclError as exc:
                self.skipTest(str(exc))
            try:
                self.assertEqual(window.login_account.cget("fg"), INPUT_TEXT)
                self.assertEqual(window.login_password.cget("fg"), INPUT_TEXT)
                self.assertEqual(window.register_email.cget("fg"), INPUT_TEXT)
                self.assertEqual(window.register_code.cget("fg"), INPUT_TEXT)
                self.assertEqual(window.register_username.cget("fg"), INPUT_TEXT)
                self.assertEqual(window.register_password.cget("fg"), INPUT_TEXT)
                self.assertEqual(window.register_confirm.cget("fg"), INPUT_TEXT)
            finally:
                window._close()

    def test_registration_email_validation_and_cooldown(self) -> None:
        class FakeService:
            store = SimpleNamespace(list_accounts=lambda: [])

            @staticmethod
            def send_registration_code(_email):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            paths = SimpleNamespace(resource_dir=Path(tmp))
            try:
                window = AuthWindow(paths, FakeService())
            except tk.TclError as exc:
                self.skipTest(str(exc))
            try:
                window._show_screen("register")
                with mock.patch("weimi_manager.ui.auth_window.messagebox.showwarning") as warning:
                    window._begin_registration()
                    self.assertIn(
                        "请输入",
                        str(warning.call_args),
                    )
                    window.register_email.insert(0, "not-an-email")
                    window._begin_registration()
                    self.assertIn(
                        "邮箱格式不正确",
                        str(warning.call_args),
                    )

                window.register_email.delete(0, "end")
                window.register_email.insert(0, "tester@example.com")
                window.register_username.insert(0, "tester")
                window.register_password.insert(0, "password123")
                window.register_confirm.insert(0, "password123")
                window._begin_registration()
                deadline = datetime.now().timestamp() + 2
                while window._register_countdown == 0 and datetime.now().timestamp() < deadline:
                    try:
                        window.update()
                    except tk.TclError:
                        break
                self.assertEqual(window._register_countdown, 60)
                self.assertEqual(
                    str(window.register_send_button["state"]),
                    "disabled",
                )
                self.assertIn("60s", window.register_send_button["text"])
            finally:
                window._close()

    def test_registration_returns_to_login_with_username_prefilled(self) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        tokens = SessionTokens(
            user_id="user-1",
            account_id="user-1",
            username="qqwww",
            email="qqwww@example.com",
            access_token="access",
            refresh_token="refresh",
            expires_at=now,
        )
        context = AuthContext(
            tokens=tokens,
            license=LicenseStatus(
                active=True,
                permanent=False,
                expires_at=now,
                status_text="体验中",
                remaining_days=3,
                max_devices=1,
                device_bound=True,
                server_time=now,
            ),
            device_hash="d" * 64,
            device_name="test",
        )

        class FakeService:
            store = SimpleNamespace(list_accounts=lambda: [])

            def complete_registration_with_code(
                self,
                _email,
                _code,
                _username,
                _password,
                _remember,
            ):
                return context

        with tempfile.TemporaryDirectory() as tmp:
            paths = SimpleNamespace(resource_dir=Path(tmp))
            try:
                window = AuthWindow(paths, FakeService())
            except tk.TclError as exc:
                self.skipTest(str(exc))
            try:
                window._show_screen("register")
                window.register_username.insert(0, "qqwww")
                window._register_email = "qqwww@example.com"
                window.register_code.configure(state="normal")
                window.register_code.insert(0, "123456")
                window.register_password.insert(0, "password123")
                window.register_confirm.insert(0, "password123")
                window.register_verify_button.configure(state="normal")
                window._verify_registration()
                deadline = datetime.now().timestamp() + 2
                while window.result is None and datetime.now().timestamp() < deadline:
                    try:
                        window.update()
                    except tk.TclError:
                        break
                self.assertIsNotNone(window.result)
                self.assertEqual(window.result.username, "qqwww")
            finally:
                window._close()


class MobileSalesAuthClientTests(unittest.TestCase):
    def test_mobile_sales_owner_enqueue_uses_typed_rpc_payload(self) -> None:
        client = object.__new__(SupabaseAuthClient)
        client.config = SimpleNamespace(
            rest_url=lambda path: f"https://example.invalid/{path}"
        )
        calls: list[tuple] = []

        def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return {"task_id": "task-1"}

        client._request = request
        result = client.mobile_sales_owner_enqueue_task(
            "access",
            device_id="device-1",
            task_id="task-1",
            source_url="https://example.invalid/product",
            title="商品",
            purchase_quantity=3,
            price_cap_cents=1234,
        )

        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(calls[0][0], "POST")
        self.assertTrue(calls[0][1].endswith("mobile_sales_owner_enqueue_task"))
        payload = calls[0][2]["payload"]
        self.assertEqual(payload["p_device_id"], "device-1")
        self.assertEqual(payload["p_purchase_quantity"], 3)
        self.assertEqual(payload["p_price_cap_cents"], 1234)

    def test_mobile_sales_owner_list_tasks_returns_device_rows(self) -> None:
        client = object.__new__(SupabaseAuthClient)
        client.config = SimpleNamespace(
            rest_url=lambda path: f"https://example.invalid/{path}"
        )
        calls: list[tuple] = []

        def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return [
                {"task_id": "task-1", "lease_expires_at": "2026-11-17T00:00:00Z"},
                "invalid",
            ]

        client._request = request
        result = client.mobile_sales_owner_list_tasks(
            "access",
            device_id="device-1",
        )

        self.assertEqual(result, [
            {"task_id": "task-1", "lease_expires_at": "2026-11-17T00:00:00Z"}
        ])
        self.assertEqual(calls[0][2]["payload"], {"p_device_id": "device-1"})


if __name__ == "__main__":
    unittest.main()
