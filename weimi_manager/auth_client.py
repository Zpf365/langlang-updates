from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any

import requests

from .auth_config import AuthConfig

logger = logging.getLogger(__name__)


class AuthError(RuntimeError):
    def __init__(
        self,
        message: str,
        status: int = 0,
        code: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class SessionTokens:
    user_id: str
    account_id: str
    username: str
    email: str
    access_token: str
    refresh_token: str
    expires_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SessionTokens":
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        metadata = user.get("user_metadata") if isinstance(user.get("user_metadata"), dict) else {}
        expires_in = int(payload.get("expires_in") or 3600)
        return cls(
            user_id=str(user.get("id") or payload.get("user_id") or ""),
            account_id=str(user.get("id") or payload.get("account_id") or ""),
            username=str(
                payload.get("username")
                or metadata.get("username")
                or metadata.get("display_name")
                or ""
            ),
            email=str(user.get("email") or payload.get("email") or ""),
            access_token=str(payload.get("access_token") or ""),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_at=(datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(
                timespec="seconds"
            ),
            metadata=dict(metadata),
        )


@dataclass(frozen=True)
class LicenseStatus:
    active: bool
    permanent: bool
    expires_at: str | None
    status_text: str
    remaining_days: int
    max_devices: int
    device_bound: bool
    server_time: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LicenseStatus":
        return cls(
            active=bool(payload.get("active")),
            permanent=bool(payload.get("permanent")),
            expires_at=(str(payload["expires_at"]) if payload.get("expires_at") else None),
            status_text=str(payload.get("status_text") or ""),
            remaining_days=max(0, int(payload.get("remaining_days") or 0)),
            max_devices=max(1, int(payload.get("max_devices") or 1)),
            device_bound=bool(payload.get("device_bound")),
            server_time=str(payload.get("server_time") or ""),
        )


class SupabaseAuthClient:
    def __init__(
        self,
        config: AuthConfig,
        session: requests.Session | None = None,
        direct_session: requests.Session | None = None,
    ) -> None:
        config.require_configured()
        self.config = config
        self.session = session or requests.Session()
        self._direct_session = direct_session
        self._session_tokens: SessionTokens | None = None
        self._on_session_refreshed: Any = None
        self._refresh_lock = RLock()

    @property
    def session_tokens(self) -> SessionTokens | None:
        return self._session_tokens

    @property
    def current_access_token(self) -> str:
        tokens = self._session_tokens
        return tokens.access_token if tokens is not None else ""

    def bind_session(
        self,
        tokens: SessionTokens,
        on_refresh: Any = None,
    ) -> None:
        """Bind the active account so expired access tokens can be renewed."""
        self._session_tokens = tokens
        if on_refresh is not None:
            self._on_session_refreshed = on_refresh

    def _headers(self, access_token: str = "") -> dict[str, str]:
        headers = {
            "apikey": self.config.publishable_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        token = access_token or self.config.publishable_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _error_payload(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _error_message(cls, response: requests.Response) -> str:
        payload = cls._error_payload(response)
        for key in ("message", "msg", "error_description", "error", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
        return f"账号服务返回错误（HTTP {response.status_code}）"

    @classmethod
    def _is_expired_session_response(
        cls,
        response: requests.Response,
    ) -> bool:
        if response.status_code != 401:
            return False
        message = cls._error_message(response).casefold()
        return any(
            marker in message
            for marker in (
                "jwt expired",
                "token has expired",
                "token expired",
                "invalid jwt",
            )
        )

    def _refresh_bound_session(
        self,
        failed_access_token: str,
    ) -> SessionTokens:
        with self._refresh_lock:
            current = self._session_tokens
            if current is None or not current.refresh_token:
                raise AuthError(
                    "登录状态已过期，请重新登录。",
                    401,
                    "SESSION_EXPIRED",
                )
            if (
                current.access_token
                and current.access_token != failed_access_token
            ):
                return current
            try:
                refreshed = self.refresh(current.refresh_token)
            except AuthError as exc:
                raise AuthError(
                    "登录状态已过期，请重新登录。",
                    401,
                    "SESSION_EXPIRED",
                ) from exc
            if not refreshed.access_token or not refreshed.refresh_token:
                raise AuthError(
                    "登录状态已过期，请重新登录。",
                    401,
                    "SESSION_EXPIRED",
                )
            if not refreshed.username:
                refreshed = SessionTokens(
                    user_id=refreshed.user_id or current.user_id,
                    account_id=refreshed.account_id or current.account_id,
                    username=current.username,
                    email=refreshed.email or current.email,
                    access_token=refreshed.access_token,
                    refresh_token=refreshed.refresh_token,
                    expires_at=refreshed.expires_at,
                    metadata=refreshed.metadata or current.metadata,
                )
            self._session_tokens = refreshed
            callback = self._on_session_refreshed
            if callback is not None:
                try:
                    callback(refreshed)
                except Exception:
                    logger.exception("保存刷新后的登录会话失败")
            return refreshed

    def refresh_current_session(
        self,
        failed_access_token: str = "",
    ) -> SessionTokens:
        """Refresh the bound login after an authenticated request gets 401."""
        current = self._session_tokens
        failed = failed_access_token or (
            current.access_token if current is not None else ""
        )
        return self._refresh_bound_session(failed)

    def _request(
        self,
        method: str,
        url: str,
        *,
        access_token: str = "",
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
        retry_after_refresh: bool = True,
        retry_direct_without_proxy: bool = False,
    ) -> Any:
        if access_token and self._session_tokens is not None:
            access_token = self._session_tokens.access_token or access_token
        request_kwargs = {
            "headers": self._headers(access_token),
            "json": payload,
            "timeout": self.config.request_timeout,
        }
        try:
            response = self.session.request(
                method,
                url,
                **request_kwargs,
            )
        except requests.RequestException as exc:
            proxies = getattr(self.session, "proxies", None) or (
                requests.utils.get_environ_proxies(url)
            )
            if not retry_direct_without_proxy or not proxies:
                if isinstance(exc, requests.Timeout):
                    raise AuthError(
                        "连接账号服务器超时，请检查网络后重试。",
                        code="AUTH_TIMEOUT",
                    ) from exc
                raise AuthError(
                    "无法连接账号服务器，请检查网络后重试。",
                    code="AUTH_CONNECTION_FAILED",
                ) from exc
            logger.warning(
                "账号服务器经系统代理请求失败，正在尝试直连：%s",
                type(exc).__name__,
            )
            direct = self._direct_session
            if direct is None:
                direct = requests.Session()
                direct.trust_env = False
                self._direct_session = direct
            try:
                response = direct.request(method, url, **request_kwargs)
            except requests.RequestException as direct_exc:
                logger.warning(
                    "账号服务器直连仍失败：%s",
                    type(direct_exc).__name__,
                )
                if isinstance(direct_exc, requests.Timeout):
                    raise AuthError(
                        "连接账号服务器超时，代理和直连均失败，请检查网络。",
                        code="AUTH_TIMEOUT",
                    ) from direct_exc
                raise AuthError(
                    "无法连接账号服务器，代理和直连均失败，请检查网络。",
                    code="AUTH_CONNECTION_FAILED",
                ) from direct_exc
        if (
            retry_after_refresh
            and access_token
            and self._session_tokens is not None
            and self._is_expired_session_response(response)
        ):
            refreshed = self._refresh_bound_session(access_token)
            return self._request(
                method,
                url,
                access_token=refreshed.access_token,
                payload=payload,
                expected=expected,
                retry_after_refresh=False,
                retry_direct_without_proxy=retry_direct_without_proxy,
            )
        if response.status_code not in expected:
            error_payload = self._error_payload(response)
            if (
                access_token
                and self._is_expired_session_response(response)
            ):
                raise AuthError(
                    "登录状态已过期，请重新登录。",
                    401,
                    "SESSION_EXPIRED",
                )
            raise AuthError(
                self._error_message(response),
                response.status_code,
                str(error_payload.get("code") or ""),
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise AuthError("账号服务器返回了无法识别的数据。") from exc
        return data

    def reserve_username(self, email: str, username: str) -> None:
        available = self._request(
            "POST",
            self.config.rest_url("rpc/check_username_available"),
            payload={"p_username": username.strip()},
            expected=(200, 201),
        )
        if available is False:
            raise AuthError("账户名已存在", 409)

    def sign_up(self, email: str, password: str, username: str) -> dict[str, Any]:
        self.reserve_username(email, username)
        return self._request(
            "POST",
            self.config.auth_url("signup"),
            payload={
                "email": email.strip(),
                "password": password,
                "data": {"username": username.strip()},
            },
            expected=(200,),
        )

    def resend_signup_code(self, email: str) -> None:
        self._request(
            "POST",
            self.config.auth_url("resend"),
            payload={"type": "signup", "email": email.strip()},
            expected=(200,),
        )

    def send_signup_otp(self, email: str) -> None:
        self._request(
            "POST",
            self.config.auth_url("otp"),
            payload={
                "email": email.strip(),
                "create_user": True,
            },
            expected=(200,),
        )

    def verify_email_otp(
        self,
        email: str,
        code: str,
        username: str,
    ) -> SessionTokens | None:
        data = self._request(
            "POST",
            self.config.auth_url("verify"),
            payload={
                "type": "email",
                "email": email.strip(),
                "token": code.strip(),
            },
            expected=(200,),
        )
        if not data.get("access_token"):
            return None
        tokens = SessionTokens.from_payload(data)
        metadata = dict(tokens.metadata)
        metadata["username"] = username.strip()
        return SessionTokens(
            user_id=tokens.user_id,
            account_id=tokens.account_id,
            username=username.strip(),
            email=tokens.email,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            metadata=metadata,
        )

    def verify_signup(
        self,
        email: str,
        code: str,
    ) -> SessionTokens | None:
        data = self._request(
            "POST",
            self.config.auth_url("verify"),
            payload={
                "type": "signup",
                "email": email.strip(),
                "token": code.strip(),
            },
            expected=(200,),
        )
        if not data.get("access_token"):
            return None
        return SessionTokens.from_payload(data)

    def complete_registration(self, tokens: SessionTokens) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/complete_registration"),
            access_token=tokens.access_token,
            payload={"p_username": tokens.username},
            expected=(200, 201),
        )
        if not isinstance(data, dict):
            raise AuthError("注册资料保存失败。")
        return data

    def login_by_username(
        self,
        username: str,
        password: str,
    ) -> SessionTokens:
        data = self._request(
            "POST",
            self.config.function_url("login-by-username"),
            payload={
                "username": username.strip(),
                "password": password,
            },
            expected=(200,),
            retry_direct_without_proxy=True,
        )
        if isinstance(data, dict):
            data["username"] = username.strip()
        tokens = SessionTokens.from_payload(data)
        if not tokens.access_token or not tokens.refresh_token:
            raise AuthError("登录成功但未取得有效会话，请稍后重试。")
        return tokens

    def login_by_email(
        self,
        email: str,
        password: str,
    ) -> SessionTokens:
        data = self._request(
            "POST",
            f"{self.config.auth_url('token')}?grant_type=password",
            payload={
                "email": email.strip(),
                "password": password,
            },
            expected=(200,),
            retry_direct_without_proxy=True,
        )
        return SessionTokens.from_payload(data)

    def refresh(self, refresh_token: str) -> SessionTokens:
        data = self._request(
            "POST",
            f"{self.config.auth_url('token')}?grant_type=refresh_token",
            payload={"refresh_token": refresh_token},
            expected=(200,),
        )
        return SessionTokens.from_payload(data)

    def logout(self, access_token: str) -> None:
        try:
            self._request(
                "POST",
                self.config.auth_url("logout"),
                access_token=access_token,
                expected=(200, 204),
            )
        except AuthError:
            return

    def send_recovery_code(self, identifier: str) -> str:
        identifier = identifier.strip()
        if "@" in identifier:
            email = identifier.lower()
        else:
            email_data = self._request(
                "POST",
                self.config.rest_url("rpc/lookup_login_email"),
                payload={"p_username": identifier},
                expected=(200,),
            )
            email = str(email_data or "").strip()
        if not email:
            raise AuthError("无法确定该账户的绑定邮箱。")
        self._request(
            "POST",
            self.config.auth_url("otp"),
            payload={"email": email, "create_user": False},
            expected=(200,),
        )
        return email

    def verify_recovery_code(
        self,
        email: str,
        code: str,
    ) -> SessionTokens:
        data = self._request(
            "POST",
            self.config.auth_url("verify"),
            payload={
                "type": "email",
                "email": email.strip(),
                "token": code.strip(),
            },
            expected=(200,),
        )
        tokens = SessionTokens.from_payload(data)
        if not tokens.access_token:
            raise AuthError("验证码验证失败，请重新获取验证码。")
        return tokens

    def update_password(self, access_token: str, password: str) -> None:
        self._request(
            "PUT",
            self.config.auth_url("user"),
            access_token=access_token,
            payload={"password": password},
            expected=(200,),
        )

    def get_user(self, access_token: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            self.config.auth_url("user"),
            access_token=access_token,
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def update_user_metadata(
        self,
        access_token: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        data = self._request(
            "PUT",
            self.config.auth_url("user"),
            access_token=access_token,
            payload={"data": metadata},
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def organization_context(self, access_token: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/ensure_my_organization"),
            access_token=access_token,
            payload={},
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def list_team_members(self, access_token: str) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/list_my_team_members"),
            access_token=access_token,
            payload={},
            expected=(200,),
        )
        return [
            item
            for item in data
            if isinstance(item, dict)
        ] if isinstance(data, list) else []

    def pull_organization_vault(
        self,
        access_token: str,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/pull_organization_vault"),
            access_token=access_token,
            payload={},
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def push_organization_vault(
        self,
        access_token: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/push_organization_vault"),
            access_token=access_token,
            payload={"p_snapshot": snapshot},
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def delete_organization_stores(
        self,
        access_token: str,
        app_ids: list[str],
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/delete_organization_stores"),
            access_token=access_token,
            payload={"p_app_ids": app_ids},
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def authorize_organization_command(
        self,
        access_token: str,
        request_id: str,
        command: str,
        store_app_id: str = "",
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url(
                "rpc/authorize_organization_command"
            ),
            access_token=access_token,
            payload={
                "p_request_id": request_id,
                "p_command": command,
                "p_store_app_id": store_app_id,
            },
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def complete_organization_command(
        self,
        access_token: str,
        request_id: str,
        succeeded: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url(
                "rpc/complete_organization_command"
            ),
            access_token=access_token,
            payload={
                "p_request_id": request_id,
                "p_succeeded": succeeded,
                "p_result": result or {},
                "p_error": error,
            },
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def upsert_organization_order_workbench(
        self,
        access_token: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url(
                "rpc/upsert_organization_order_workbench"
            ),
            access_token=access_token,
            payload={"p_rows": rows},
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def pull_organization_order_workbench(
        self,
        access_token: str,
        store_app_ids: list[str],
    ) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            self.config.rest_url(
                "rpc/pull_organization_order_workbench"
            ),
            access_token=access_token,
            payload={"p_store_app_ids": store_app_ids},
            expected=(200,),
        )
        return [
            item for item in data if isinstance(item, dict)
        ] if isinstance(data, list) else []

    def upsert_organization_settings(
        self,
        access_token: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/upsert_organization_settings"),
            access_token=access_token,
            payload={"p_items": items},
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def pull_organization_settings(
        self,
        access_token: str,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/pull_organization_settings"),
            access_token=access_token,
            payload={},
            expected=(200,),
        )
        return [
            item for item in data if isinstance(item, dict)
        ] if isinstance(data, list) else []

    def mobile_sales_list_devices(
        self,
        access_token: str,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/mobile_sales_list_devices"),
            access_token=access_token,
            payload={},
            expected=(200,),
        )
        return [
            item for item in data if isinstance(item, dict)
        ] if isinstance(data, list) else []

    def mobile_sales_owner_enqueue_task(
        self,
        access_token: str,
        *,
        device_id: str,
        task_id: str,
        source_url: str,
        title: str = "",
        sku_selection_mode: str = "any",
        purchase_quantity: int = 1,
        price_cap_cents: int = 0,
        specified_sku_id: str = "",
        specified_specs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.rest_url(
                "rpc/mobile_sales_owner_enqueue_task"
            ),
            access_token=access_token,
            payload={
                "p_device_id": device_id,
                "p_task_id": task_id,
                "p_source_url": source_url,
                "p_title": title,
                "p_sku_selection_mode": sku_selection_mode,
                "p_purchase_quantity": int(purchase_quantity),
                "p_price_cap_cents": int(price_cap_cents),
                "p_specified_sku_id": specified_sku_id,
                "p_specified_specs": specified_specs or {},
            },
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def mobile_sales_owner_list_tasks(
        self,
        access_token: str,
        *,
        device_id: str,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/mobile_sales_owner_list_tasks"),
            access_token=access_token,
            payload={"p_device_id": device_id},
            expected=(200,),
        )
        return [
            item for item in data if isinstance(item, dict)
        ] if isinstance(data, list) else []

    def team_admin(
        self,
        access_token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.function_url("team-admin"),
            access_token=access_token,
            payload=payload,
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}

    def register_device(
        self,
        tokens: SessionTokens,
        device_hash: str,
        device_name: str,
        app_version: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            self.config.rest_url("rpc/register_my_device"),
            access_token=tokens.access_token,
            payload={
                "p_device_hash": device_hash,
                "p_device_name": device_name,
                "p_app_version": app_version,
            },
            expected=(200,),
        )

    def license_status(self, tokens: SessionTokens) -> LicenseStatus:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/my_license_status"),
            access_token=tokens.access_token,
            payload={},
            expected=(200,),
        )
        return LicenseStatus.from_payload(data)

    def redeem_card(
        self,
        tokens: SessionTokens,
        code: str,
        device_hash: str,
    ) -> LicenseStatus:
        data = self._request(
            "POST",
            self.config.rest_url("rpc/redeem_my_license_card"),
            access_token=tokens.access_token,
            payload={
                "p_code": code.strip(),
                "p_device_hash": device_hash,
            },
            expected=(200,),
        )
        return LicenseStatus.from_payload(data)

    def generate_license_cards(
        self,
        access_token: str,
        *,
        count: int,
        card_type: str,
        duration_days: int,
        max_devices: int,
        rebind_penalty_hours: int,
        notes: str = "",
        available_days: int = 0,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            self.config.function_url("admin-generate-cards"),
            access_token=access_token,
            payload={
                "count": int(count),
                "card_type": str(card_type),
                "duration_days": int(duration_days),
                "max_devices": int(max_devices),
                "rebind_penalty_hours": int(rebind_penalty_hours),
                "notes": str(notes),
                "available_days": int(available_days),
            },
            expected=(200,),
        )
        return data if isinstance(data, dict) else {}
