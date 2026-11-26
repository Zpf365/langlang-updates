from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .account_store_sync import (
    VAULT_METADATA_KEY,
    decrypt_store_snapshot,
    derive_vault_key,
    encrypt_store_snapshot,
)
from .auth_client import (
    AuthError,
    LicenseStatus,
    SessionTokens,
    SupabaseAuthClient,
)
from .auth_config import AuthConfigError
from .auth_session import (
    AuthSessionStore,
    DeviceIdentity,
    SavedAccount,
)


@dataclass(frozen=True)
class AuthContext:
    tokens: SessionTokens
    license: LicenseStatus
    device_hash: str
    device_name: str
    offline: bool = False
    organization_id: str = ""
    organization_name: str = ""
    member_id: str = ""
    member_role: str = "owner"
    permissions: dict[str, Any] = field(default_factory=dict)
    entitlements: dict[str, bool] = field(default_factory=dict)
    vault_key: bytes | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def account_id(self) -> str:
        return self.tokens.account_id

    @property
    def username(self) -> str:
        return self.tokens.username

    @property
    def email(self) -> str:
        return self.tokens.email


class AuthService:
    def __init__(
        self,
        client: SupabaseAuthClient,
        store: AuthSessionStore,
    ) -> None:
        self.client = client
        self.store = store
        self.device_hash = DeviceIdentity.device_hash()
        self.device_name = DeviceIdentity.device_name()
        self._registration_passwords: dict[str, str] = {}

    def _bind_session(
        self,
        tokens: SessionTokens,
    ) -> None:
        bind_session = getattr(self.client, "bind_session", None)
        if callable(bind_session):
            bind_session(tokens, self._persist_refreshed_tokens)

    def _persist_refreshed_tokens(
        self,
        tokens: SessionTokens,
    ) -> None:
        existing = self.store.find(tokens.username)
        if existing is None or existing.account_id != tokens.account_id:
            return
        self.store.save(
            replace(
                existing,
                email=tokens.email or existing.email,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
            )
        )

    def _save_session(
        self,
        tokens: SessionTokens,
        license_status: LicenseStatus | None = None,
        password: str = "",
        organization: dict[str, Any] | None = None,
        entitlements: dict[str, bool] | None = None,
    ) -> None:
        existing = self.store.find(tokens.username)
        saved_password = password or (
            existing.password
            if existing is not None and existing.account_id == tokens.account_id
            else ""
        )
        self.store.save(
            SavedAccount(
                account_id=tokens.account_id,
                username=tokens.username,
                email=tokens.email,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
                last_login_at=datetime.now(UTC).isoformat(timespec="seconds"),
                license_active=bool(license_status and license_status.active),
                license_permanent=bool(license_status and license_status.permanent),
                license_expires_at=str(
                    license_status.expires_at
                    if license_status and license_status.expires_at
                    else ""
                ),
                last_server_time=str(
                    license_status.server_time
                    if license_status and license_status.server_time
                    else ""
                ),
                last_verified_local_time=datetime.now(UTC).isoformat(timespec="seconds"),
                organization_id=str(
                    (organization or {}).get("organization_id")
                    or (
                        existing.organization_id
                        if existing is not None
                        and existing.account_id == tokens.account_id
                        else ""
                    )
                ),
                member_role=str(
                    (organization or {}).get("role")
                    or (
                        existing.member_role
                        if existing is not None
                        and existing.account_id == tokens.account_id
                        else "owner"
                    )
                ),
                permissions=(
                    dict((organization or {}).get("permissions") or {})
                    if isinstance((organization or {}).get("permissions"), dict)
                    else (
                        dict(existing.permissions)
                        if existing is not None
                        and existing.account_id == tokens.account_id
                        else {}
                    )
                ),
                entitlements=(
                    {
                        str(key): bool(value)
                        for key, value in entitlements.items()
                    }
                    if isinstance(entitlements, dict)
                    else (
                        dict(existing.entitlements)
                        if existing is not None
                        and existing.account_id == tokens.account_id
                        else {}
                    )
                ),
                password=saved_password,
            )
        )

    def _authorize(
        self,
        tokens: SessionTokens,
        remember: bool,
        password: str = "",
    ) -> AuthContext:
        self._bind_session(tokens)
        license_status: LicenseStatus | None = None
        organization: dict[str, Any] = {}
        organization_context = getattr(
            self.client,
            "organization_context",
            None,
        )
        if callable(organization_context):
            try:
                organization = organization_context(tokens.access_token)
            except AuthError as exc:
                raise AuthError(
                    "团队权限校验失败，请检查网络后重新登录。",
                    exc.status,
                ) from exc
            if not organization.get("organization_id") or not organization.get(
                "role"
            ):
                raise AuthError(
                    "团队权限数据不完整，请稍后重试。",
                    503,
                )
        entitlements: dict[str, bool] = {}
        capabilities_reader = getattr(
            self.client,
            "my_ops_capabilities",
            None,
        )
        if callable(capabilities_reader):
            try:
                entitlements = capabilities_reader(tokens.access_token)
            except AuthError:
                # Capability lookup is fail-closed; core account functions
                # remain available while all protected modules stay hidden.
                entitlements = {}
        try:
            self.client.register_device(
                tokens,
                self.device_hash,
                self.device_name,
                __version__,
            )
            license_status = self.client.license_status(tokens)
        except AuthError:
            if remember:
                self._save_session(
                    tokens,
                    license_status,
                    password,
                    organization,
                    entitlements,
                )
            raise
        if remember:
            self._save_session(
                tokens,
                license_status,
                password,
                organization,
                entitlements,
            )
        return AuthContext(
            tokens=tokens,
            license=license_status,
            device_hash=self.device_hash,
            device_name=self.device_name,
            offline=False,
            organization_id=str(
                organization.get("organization_id") or ""
            ),
            organization_name=str(
                organization.get("organization_name") or ""
            ),
            member_id=str(organization.get("member_id") or ""),
            member_role=str(organization.get("role") or "owner"),
            permissions=(
                dict(organization.get("permissions") or {})
                if isinstance(organization.get("permissions"), dict)
                else {}
            ),
            entitlements=dict(entitlements),
            vault_key=(
                derive_vault_key(password, tokens.account_id)
                if password and tokens.account_id
                else None
            ),
        )

    def login(
        self,
        username: str,
        password: str,
        remember: bool,
    ) -> AuthContext:
        tokens = self.client.login_by_username(username, password)
        return self._authorize(tokens, remember, password)

    def change_password(
        self,
        username: str,
        current_password: str,
        new_password: str,
    ) -> None:
        if len(new_password) < 8:
            raise AuthError("新密码至少需要 8 位。")
        if current_password == new_password:
            raise AuthError("新密码不能与当前密码相同。")
        tokens = self.client.login_by_username(
            username,
            current_password,
        )
        organization_context = getattr(
            self.client,
            "organization_context",
            None,
        )
        if callable(organization_context):
            organization = organization_context(tokens.access_token)
            if (
                isinstance(organization, dict)
                and organization.get("role") not in {None, "", "owner", "admin"}
            ):
                raise AuthError(
                    "子账号不能自行修改密码，请联系主账号重置。",
                    403,
                )
        metadata = dict(tokens.metadata)
        envelope = metadata.get(VAULT_METADATA_KEY)
        new_envelope = None
        if isinstance(envelope, dict) and tokens.account_id:
            old_key = derive_vault_key(
                current_password,
                tokens.account_id,
            )
            snapshot = decrypt_store_snapshot(
                envelope,
                old_key,
                tokens.account_id,
            )
            new_key = derive_vault_key(
                new_password,
                tokens.account_id,
            )
            new_envelope = encrypt_store_snapshot(
                snapshot,
                new_key,
                tokens.account_id,
            )
        self.client.update_password(tokens.access_token, new_password)
        if new_envelope is not None:
            self.client.update_user_metadata(
                tokens.access_token,
                {VAULT_METADATA_KEY: new_envelope},
            )
        self._save_session(tokens, None, new_password)

    def begin_registration(
        self,
        email: str,
        username: str,
        password: str,
    ) -> None:
        self.client.sign_up(email, password, username)
        self._registration_passwords[email.strip().casefold()] = password

    def complete_registration(
        self,
        email: str,
        code: str,
        remember: bool = True,
    ) -> AuthContext:
        tokens = self.client.verify_signup(email, code)
        if tokens is None:
            raise AuthError("邮箱尚未验证成功，请检查验证码。")
        self.client.complete_registration(tokens)
        password = self._registration_passwords.pop(
            email.strip().casefold(),
            "",
        )
        return self._authorize(tokens, remember, password)

    def resend_registration_code(self, email: str) -> None:
        self.client.send_signup_otp(email)

    def send_registration_code(self, email: str) -> None:
        self.client.send_signup_otp(email)

    def complete_registration_with_code(
        self,
        email: str,
        code: str,
        username: str,
        password: str,
        remember: bool = True,
    ) -> AuthContext:
        self.client.reserve_username(email, username)
        tokens = self.client.verify_email_otp(
            email,
            code,
            username,
        )
        if tokens is None:
            raise AuthError("邮箱验证码无效或已经过期。")
        self.client.update_password(tokens.access_token, password)
        self.client.complete_registration(tokens)
        return self._authorize(tokens, remember, password)

    def complete_registration_after_link(
        self,
        email: str,
        password: str,
        remember: bool = True,
    ) -> AuthContext:
        tokens = self.client.login_by_email(email, password)
        if not tokens.username:
            raise AuthError("邮箱尚未确认，请先点击注册邮件中的确认链接。")
        self.client.complete_registration(tokens)
        return self._authorize(tokens, remember, password)

    def send_recovery_code(self, identifier: str) -> str:
        return self.client.send_recovery_code(identifier)

    def reset_password(
        self,
        email: str,
        code: str,
        new_password: str,
    ) -> None:
        tokens = self.client.verify_recovery_code(email, code)
        self.client.update_password(tokens.access_token, new_password)
        self.client.logout(tokens.access_token)
        saved = self.store.find(tokens.username)
        if saved is not None:
            self.store.remove(saved.account_id)

    def redeem(self, context: AuthContext, code: str) -> AuthContext:
        license_status = self.client.redeem_card(
            context.tokens,
            code,
            self.device_hash,
        )
        return AuthContext(
            tokens=context.tokens,
            license=license_status,
            device_hash=self.device_hash,
            device_name=self.device_name,
            offline=context.offline,
            organization_id=context.organization_id,
            organization_name=context.organization_name,
            member_id=context.member_id,
            member_role=context.member_role,
            permissions=context.permissions,
            entitlements=context.entitlements,
            vault_key=context.vault_key,
        )

    def logout(
        self,
        context: AuthContext,
        *,
        forget_local: bool = False,
    ) -> None:
        error: Exception | None = None
        try:
            self.client.logout(context.tokens.access_token)
        except Exception as exc:
            error = exc
        finally:
            if forget_local:
                self.store.remove(context.account_id)
        if error is not None:
            raise error

    def restore_account(self, account: SavedAccount) -> AuthContext:
        try:
            tokens = self.client.refresh(account.refresh_token)
        except AuthError as exc:
            if exc.status == 0:
                return self._offline_context(account)
            raise
        if not tokens.username:
            tokens = SessionTokens(
                user_id=tokens.user_id,
                account_id=tokens.account_id,
                username=account.username,
                email=tokens.email or account.email,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
                metadata=tokens.metadata,
            )
        return self._authorize(tokens, True)

    def _offline_context(self, account: SavedAccount) -> AuthContext:
        """Allow one hour of offline use after the last successful check."""
        if not account.last_verified_local_time or not account.last_server_time:
            raise AuthError("当前离线，且没有可用的本地授权记录。")
        try:
            last_local = datetime.fromisoformat(
                account.last_verified_local_time.replace("Z", "+00:00")
            )
            server_time = datetime.fromisoformat(account.last_server_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AuthError("本地授权记录已损坏，请联网后重新登录。") from exc
        now = datetime.now(UTC)
        if now < last_local - timedelta(minutes=5):
            raise AuthError("检测到系统时间异常，请联网校准后重新登录。")
        elapsed = now - last_local
        if elapsed > timedelta(hours=1):
            raise AuthError("离线使用时间已超过 1 小时，请联网后重新登录。")
        estimated_server = server_time + elapsed
        permanent = bool(account.license_permanent)
        expiry: datetime | None = None
        if account.license_expires_at:
            try:
                expiry = datetime.fromisoformat(account.license_expires_at.replace("Z", "+00:00"))
            except ValueError:
                expiry = None
        active = permanent or bool(expiry and expiry > estimated_server)
        remaining_days = (
            999999
            if permanent
            else max(
                0,
                int((expiry - estimated_server).total_seconds() // 86400) + 1,
            )
            if expiry
            else 0
        )
        tokens = SessionTokens(
            user_id=account.account_id,
            account_id=account.account_id,
            username=account.username,
            email=account.email,
            access_token=account.access_token,
            refresh_token=account.refresh_token,
            expires_at=account.expires_at,
        )
        self._bind_session(tokens)
        return AuthContext(
            tokens=tokens,
            license=LicenseStatus(
                active=active,
                permanent=permanent,
                expires_at=account.license_expires_at or None,
                status_text="离线宽限中" if active else "授权已到期",
                remaining_days=remaining_days,
                max_devices=1,
                device_bound=True,
                server_time=estimated_server.isoformat(timespec="seconds"),
            ),
            device_hash=self.device_hash,
            device_name=self.device_name,
            offline=True,
            organization_id=account.organization_id,
            member_role=account.member_role,
            permissions=dict(account.permissions),
            entitlements=dict(account.entitlements),
        )


def build_auth_service(
    config,
    auth_dir: Path,
) -> AuthService:
    if not config.configured:
        raise AuthConfigError("远程账号服务尚未配置。")
    return AuthService(
        SupabaseAuthClient(config),
        AuthSessionStore(auth_dir),
    )
