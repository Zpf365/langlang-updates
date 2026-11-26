from __future__ import annotations

import hashlib
import hmac
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .auth_client import AuthError, LicenseStatus, SessionTokens
from .auth_service import AuthContext
from .auth_session import AuthSessionStore, SavedAccount


class TesterAuthService:
    """Offline account gateway for explicitly configured tester builds."""

    def __init__(
        self,
        resource_dir: Path,
        data_dir: Path,
        remote_service: Any | None = None,
    ) -> None:
        self.paths = self._account_files(resource_dir)
        self.path = (
            self.paths[0]
            if self.paths
            else resource_dir / "assets" / "tester_accounts.json"
        )
        self.enabled = False
        self.accounts: dict[str, dict[str, Any]] = {}
        for path in self.paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.enabled = self.enabled or bool(payload.get("enabled"))
            self.accounts.update(
                {
                    str(item["username"]).casefold(): item
                    for item in payload.get("accounts", [])
                    if isinstance(item, dict) and item.get("username")
                }
            )
        if self.enabled and not self.accounts:
            raise RuntimeError("直接登录账号配置为空")
        self.store = AuthSessionStore(data_dir.parent / "auth")
        self.data_dir = data_dir
        self.remote_service = remote_service

    @property
    def client(self) -> Any | None:
        return getattr(self.remote_service, "client", None)

    def _remote(self) -> Any:
        if self.remote_service is None:
            raise AuthError("远程账号服务尚未配置", 503)
        return self.remote_service

    def _is_local_account(self, account_id: str) -> bool:
        return any(
            str(account.get("account_id") or "") == account_id
            for account in self.accounts.values()
        )

    @staticmethod
    def _account_files(resource_dir: Path) -> tuple[Path, ...]:
        assets = resource_dir / "assets"
        return tuple(
            path
            for name in ("tester_accounts.json", "universal_login.json")
            if (path := assets / name).is_file()
        )

    @classmethod
    def _find_account_file(cls, resource_dir: Path) -> Path:
        paths = cls._account_files(resource_dir)
        return paths[0] if paths else resource_dir / "assets" / "tester_accounts.json"

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            240_000,
        ).hex()

    def login(
        self,
        username: str,
        password: str,
        remember: bool,
    ) -> AuthContext:
        key = username.strip().casefold()
        account = self.accounts.get(key)
        if self.remote_service is not None:
            try:
                return self._remote().login(
                    username,
                    password,
                    remember,
                )
            except AuthError as exc:
                if exc.code != "ACCOUNT_NOT_FOUND":
                    raise
        if account is None:
            raise AuthError("账号不存在", 404, "ACCOUNT_NOT_FOUND")
        expected = str(account.get("password_hash") or "")
        actual = self._hash(password, str(account.get("salt") or ""))
        if not hmac.compare_digest(actual, expected):
            raise AuthError("测试账号或密码错误", 401)
        account_id = str(account.get("account_id") or "")
        display = str(account.get("username") or key)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        tokens = SessionTokens(
            user_id=account_id,
            account_id=account_id,
            username=display,
            email=str(account.get("email") or ""),
            access_token="",
            refresh_token="",
            expires_at=now,
            metadata={"tester": True},
        )
        context = AuthContext(
            tokens=tokens,
            license=LicenseStatus(
                active=True,
                permanent=True,
                expires_at=None,
                status_text="测试永久授权",
                remaining_days=999999,
                max_devices=1,
                device_bound=True,
                server_time=now,
            ),
            device_hash=hashlib.sha256(
                f"{platform.node()}:{account_id}".encode("utf-8")
            ).hexdigest(),
            device_name=platform.node(),
            offline=True,
        )
        if remember:
            self.store.save(
                SavedAccount(
                    account_id=account_id,
                    username=display,
                    email=tokens.email,
                    access_token="",
                    refresh_token="",
                    expires_at=now,
                    last_login_at=now,
                    license_active=True,
                    license_permanent=True,
                    last_server_time=now,
                    last_verified_local_time=now,
                    password=password,
                )
            )
        return context

    def logout(
        self,
        context: AuthContext,
        *,
        forget_local: bool = False,
    ) -> None:
        error: Exception | None = None
        if self.remote_service is not None and context.tokens.access_token:
            try:
                self._remote().logout(
                    context,
                    forget_local=False,
                )
            except Exception as exc:
                error = exc
        if forget_local:
            self.store.remove(context.account_id)
        if error is not None:
            raise error

    def restore_account(self, account: SavedAccount) -> AuthContext:
        if not self._is_local_account(account.account_id):
            return self._remote().restore_account(account)
        raise AuthError("测试版请使用预设账号和密码登录", 401)

    def begin_registration(
        self,
        email: str,
        username: str,
        password: str,
    ) -> None:
        self._remote().begin_registration(email, username, password)

    def complete_registration(
        self,
        email: str,
        code: str,
        remember: bool = True,
    ) -> AuthContext:
        return self._remote().complete_registration(email, code, remember)

    def resend_registration_code(self, email: str) -> None:
        self._remote().resend_registration_code(email)

    def send_registration_code(self, email: str) -> None:
        self._remote().send_registration_code(email)

    def complete_registration_with_code(
        self,
        email: str,
        code: str,
        username: str,
        password: str,
        remember: bool = True,
    ) -> AuthContext:
        return self._remote().complete_registration_with_code(
            email,
            code,
            username,
            password,
            remember,
        )

    def complete_registration_after_link(
        self,
        email: str,
        password: str,
        remember: bool = True,
    ) -> AuthContext:
        return self._remote().complete_registration_after_link(
            email,
            password,
            remember,
        )

    def send_recovery_code(self, identifier: str) -> str:
        return self._remote().send_recovery_code(identifier)

    def reset_password(self, email: str, code: str, new_password: str) -> None:
        self._remote().reset_password(email, code, new_password)

    def change_password(
        self,
        username: str,
        current_password: str,
        new_password: str,
    ) -> None:
        self._remote().change_password(
            username,
            current_password,
            new_password,
        )

    def redeem(self, context: AuthContext, code: str) -> AuthContext:
        if not context.tokens.metadata.get("tester"):
            return self._remote().redeem(context, code)
        return context
