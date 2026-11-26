from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .browser_runtime import browser_launch_options
from .db import SHANGHAI_TZ, Database
from .models import Store

SHOP_HOME = "https://store.weixin.qq.com/shop"
WORKBENCH_URL = "https://store.weixin.qq.com/shop/home"
MULTI_LOGIN_URL = f"{SHOP_HOME}?action=pcLogin&multiLogin=1"


class WebSessionError(RuntimeError):
    pass


_PLAYWRIGHT_PRELOAD_LOCK = threading.Lock()


def playwright_api() -> tuple[type[BaseException], Any]:
    """Load Playwright before browser work starts in worker threads.

    PyInstaller can abort inside Tcl while a worker thread lazily imports the
    large Playwright dependency graph. Importing it from the UI thread during
    session construction keeps all later browser tasks on an already-loaded
    module set.
    """
    with _PLAYWRIGHT_PRELOAD_LOCK:
        cached = globals().get("_PLAYWRIGHT_API")
        if isinstance(cached, tuple) and len(cached) == 2:
            return cached
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise WebSessionError(
                "缺少 Playwright，请安装完整运行组件"
            ) from exc
        loaded = (PlaywrightError, sync_playwright)
        globals()["_PLAYWRIGHT_API"] = loaded
        return loaded


def preload_playwright() -> None:
    playwright_api()


def normalize_cookies(cookies: Any) -> list[dict[str, Any]]:
    if isinstance(cookies, dict):
        if "cookies" in cookies and isinstance(cookies["cookies"], list):
            return normalize_cookies(cookies["cookies"])
        return [
            {"name": str(name), "value": str(value)}
            for name, value in cookies.items()
        ]
    if not isinstance(cookies, list):
        return []
    result = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            result.append(
                {
                    "name": str(name),
                    "value": str(value),
                    "domain": str(cookie.get("domain") or ""),
                    "path": str(cookie.get("path") or "/"),
                    "expires": cookie.get("expires"),
                }
            )
    return result


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{cookie['name']}={cookie['value']}"
        for cookie in normalize_cookies(cookies)
    )


def parse_cookie_header(header: str) -> list[dict[str, Any]]:
    cookies = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if name and value:
            cookies.append({"name": name, "value": value})
    if not cookies:
        raise WebSessionError("没有识别到有效的 Cookie")
    return cookies


def identity_tokens(identity: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for value in (
        identity.get("nickname"),
        identity.get("username"),
        identity.get("store_name"),
    ):
        token = str(value or "").strip()
        if len(token) < 3 or token in {"测试店铺", "店铺", "未命名店铺"}:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def matching_identities(
    text: str,
    identities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    folded = text.casefold()
    return [
        identity
        for identity in identities
        if any(token.casefold() in folded for token in identity_tokens(identity))
    ]


def _api_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("data", "result", "response"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def extract_shop_switch_accounts(payload: Any) -> list[dict[str, Any]]:
    data = _api_data(payload)
    rows = data.get("list") or data.get("shop_list") or data.get("shops")
    return [dict(row) for row in rows or [] if isinstance(row, dict)]


def _explicit_account_role(
    item: dict[str, Any],
    initial_state: dict[str, Any],
) -> str:
    user_info = initial_state.get("user_info")
    if not isinstance(user_info, dict):
        user_info = {}
    account_info = initial_state.get("account_info")
    if not isinstance(account_info, dict):
        account_info = {}
    role_text = " ".join(
        str(value or "").casefold()
        for value in (
            item.get("accountRole"),
            item.get("account_role"),
            item.get("roleName"),
            item.get("role_name"),
            item.get("memberRole"),
            item.get("member_role"),
            initial_state.get("role"),
            user_info.get("role"),
        )
    )
    if any(
        marker in role_text
        for marker in (
            "子账号",
            "成员",
            "运营",
            "客服",
            "staff",
            "member",
            "operator",
            "subaccount",
            "sub_account",
        )
    ):
        return "sub"
    if any(
        marker in role_text
        for marker in (
            "主账号",
            "超级管理员",
            "管理员",
            "店长",
            "owner",
            "super_admin",
            "admin",
        )
    ):
        return "main"
    for value in (
        item.get("isAdmin"),
        item.get("is_admin"),
        item.get("isOwner"),
        item.get("is_owner"),
        item.get("isSuperAdmin"),
        item.get("is_super_admin"),
        user_info.get("isAdmin"),
        user_info.get("is_admin"),
        account_info.get("isAdmin"),
        account_info.get("is_admin"),
    ):
        if value is True or str(value).strip().lower() in {"1", "true"}:
            return "main"
        if value is False or str(value).strip().lower() in {
            "0",
            "false",
        }:
            return "sub"
    return "unknown"


def normalize_shop_switch_account(
    item: dict[str, Any],
    initial_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    initial_state = initial_state or {}
    user_info = initial_state.get("user_info")
    if not isinstance(user_info, dict):
        user_info = {}
    account_info = initial_state.get("account_info")
    if not isinstance(account_info, dict):
        account_info = {}
    app_id = str(item.get("appid") or item.get("appId") or "").strip()
    if not app_id:
        return None
    state_app_id = str(user_info.get("appid") or "").strip()
    if state_app_id and state_app_id != app_id:
        return None
    role = _explicit_account_role(item, initial_state)
    return {
        **item,
        "app_id": app_id,
        "name": str(
            item.get("nickname")
            or user_info.get("nickName")
            or user_info.get("nickname")
            or item.get("storeName")
            or f"店铺{app_id[-4:]}"
        ).strip(),
        "account_nickname": str(
            account_info.get("nickName")
            or account_info.get("nickname")
            or ""
        ).strip(),
        "account_username": str(
            account_info.get("userName")
            or account_info.get("username")
            or ""
        ).strip(),
        "store_username": str(
            user_info.get("userName")
            or user_info.get("username")
            or ""
        ).strip(),
        "account_role": role,
        "is_admin": role == "main" if role != "unknown" else None,
        "ec_acct_logic_type": item.get("ecAcctLogicType"),
        "shop_type": item.get("shopType"),
        "headimg_url": str(
            item.get("headimgurl")
            or item.get("headimg_url")
            or user_info.get("headImgUrl")
            or ""
        ),
    }


def select_preferred_shop_accounts(
    accounts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prefer member/sub-accounts whenever the same WeChat has both roles."""
    unique: dict[str, dict[str, Any]] = {}
    for account in accounts:
        app_id = str(account.get("app_id") or "")
        if app_id:
            unique[app_id] = account
    normalized = list(unique.values())
    sub_accounts = [
        account
        for account in normalized
        if account.get("account_role") == "sub"
    ]
    main_accounts = [
        account
        for account in normalized
        if account.get("account_role") == "main"
    ]
    unknown_accounts = [
        account
        for account in normalized
        if account.get("account_role") == "unknown"
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for account in normalized:
        group_key = str(
            account.get("account_username")
            or account.get("account_nickname")
            or ""
        ).strip().casefold()
        if group_key:
            grouped.setdefault(group_key, []).append(account)
    if grouped:
        selected: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for account in normalized:
            group_key = str(
                account.get("account_username")
                or account.get("account_nickname")
                or ""
            ).strip().casefold()
            group = grouped.get(
                group_key,
                [account] if not group_key else [],
            )
            group_has_sub = any(
                item.get("account_role") == "sub"
                for item in group
            )
            if (
                account.get("account_role") == "main"
                and group_has_sub
            ):
                excluded.append(account)
            else:
                selected.append(account)
        return selected, excluded
    if sub_accounts:
        return [*sub_accounts, *unknown_accounts], main_accounts
    return normalized, []


def filter_shop_switch_accounts(
    accounts: list[dict[str, Any]],
    allowed_app_ids: set[str] | None,
) -> list[dict[str, Any]]:
    """Keep only accounts already bound in the local store list."""
    if allowed_app_ids is None:
        return accounts
    normalized = {
        str(app_id).strip().casefold()
        for app_id in allowed_app_ids
        if str(app_id).strip()
    }
    return [
        account
        for account in accounts
        if str(account.get("appid") or account.get("appId") or "")
        .strip()
        .casefold()
        in normalized
    ]


class WechatWebSession:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = data_dir
        self.use_system_proxy = os.environ.get("WEIMI_NETWORK_MODE") == "system"
        self.log_path = data_dir / "web_login.log"
        self._request_headers: dict[str, str] = {}
        preload_playwright()

    def _log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    def _store_identity(self, store: Store, refresh: bool = False) -> dict[str, str]:
        identity = self.db.get_store_identity(store.id)
        if identity_tokens(identity) and not refresh:
            return identity
        try:
            from .wechat_api import WechatShopClient

            _, secret = self.db.get_store_secret(store.id)
            if secret:
                info = WechatShopClient(store.app_id, secret).fetch_store_info()
                self.db.upsert_store_profile(store.id, info)
        except Exception as exc:
            self._log(
                f"identity fetch failed store={store.id} "
                f"type={type(exc).__name__}"
            )
        refreshed = self.db.get_store_identity(store.id)
        if identity_tokens(refreshed):
            return refreshed
        if identity_tokens(identity):
            return identity
        raise WebSessionError(
            "网页登录店铺暂无可用身份资料，请重新扫码或补充 AppSecret"
        )

    def _classify_identity(
        self,
        text: str,
        store: Store,
        identities: list[dict[str, Any]],
    ) -> tuple[str, int | None, str]:
        matches = matching_identities(text, identities)
        current = [
            identity
            for identity in matches
            if int(identity.get("store_id") or 0) == store.id
        ]
        others = [
            identity
            for identity in matches
            if int(identity.get("store_id") or 0) != store.id
        ]
        if len(matches) == 1 and current:
            return "matched", store.id, store.name
        if len(matches) > 1:
            names = "、".join(
                str(identity.get("store_name") or identity.get("nickname"))
                for identity in matches[:3]
            )
            return "ambiguous", None, names
        if others:
            actual = others[0]
            return (
                "mismatch",
                int(actual.get("store_id") or 0) or None,
                str(actual.get("store_name") or actual.get("nickname")),
            )
        return "unknown", None, ""

    def _identify_logged_in_store(
        self,
        cookies: list[dict[str, Any]],
        identities: list[dict[str, Any]],
    ) -> tuple[int | None, str]:
        session = requests.Session()
        session.trust_env = self.use_system_proxy
        try:
            response = session.get(
                WORKBENCH_URL,
                headers={
                    "Cookie": cookie_header(cookies),
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/138.0 Safari/537.36",
                    "Referer": SHOP_HOME,
                },
                timeout=(5, 12),
                allow_redirects=True,
            )
        except requests.RequestException:
            return None, ""
        lowered_url = str(response.url or "").lower()
        page_text = response.text[:500000]
        if (
            response.status_code >= 400
            or any(
                marker in lowered_url
                for marker in ("/login", "/passport", "qrcode", "scan")
            )
            or "扫码进入我的小店" in page_text
            or "请重新登录" in page_text
        ):
            return None, ""
        matches = matching_identities(page_text, identities)
        if len(matches) == 1:
            return int(matches[0].get("store_id") or 0) or None, str(
                matches[0].get("store_name")
                or matches[0].get("nickname")
                or ""
            )
        return None, "、".join(
            str(item.get("store_name") or item.get("nickname") or "")
            for item in matches[:3]
        )

    def _page_initial_state(self, page: Any) -> dict[str, Any]:
        try:
            value = page.evaluate(
                """() => {
                    const state = window.__INITIAL_STATE__ || {};
                    const user = state.userInfo || {};
                    const account = state.accountInfo || {};
                    return {
                        user_info: {
                            appid: user.appid || user.appId || "",
                            nickName: user.nickName || user.nickname || "",
                            userName: user.userName || user.username || "",
                            headImgUrl: user.headImgUrl || user.headimgurl || "",
                            isAdmin: user.isAdmin,
                            role: user.role || user.roleName || ""
                        },
                        account_info: {
                            nickName: account.nickName || account.nickname || "",
                            userName: account.userName || account.username || "",
                            isAdmin: account.isAdmin,
                            role: account.role || account.roleName || ""
                        },
                        role: state.role || ""
                    };
                }"""
            )
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _request_json(
        self,
        page: Any,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            result = page.evaluate(
                """async ({method, url, body, extraHeaders}) => {
                    const init = {
                        method,
                        credentials: "include",
                        headers: {
                            ...extraHeaders,
                            "Accept": "application/json",
                            "X-Requested-With": "XMLHttpRequest"
                        }
                    };
                    if (body !== null) {
                        init.headers["Content-Type"] = "application/json";
                        init.body = JSON.stringify(body);
                    }
                    const response = await fetch(url, init);
                    const text = await response.text();
                    let payload = null;
                    try {
                        payload = JSON.parse(text);
                    } catch (_error) {
                        payload = {message: text.slice(0, 500)};
                    }
                    return {status: response.status, payload};
                }""",
                {
                    "method": method,
                    "url": url,
                    "body": body,
                    "extraHeaders": dict(self._request_headers),
                },
            )
        except Exception as exc:
            return {
                "status": 0,
                "payload": {"message": str(exc)},
            }
        return result if isinstance(result, dict) else {}

    def _capture_official_request_headers(self, request: Any) -> None:
        """Remember the rotating headers required by official shop APIs."""
        try:
            headers = {
                str(key).lower(): str(value)
                for key, value in request.all_headers().items()
            }
        except Exception:
            return
        if not headers.get("biz_magic"):
            return
        names = (
            "biz_magic",
            "logistics_magic",
            "mcn_magic",
            "potter-scene",
            "supplier_magic",
            "talent_magic",
            "wecom_magic",
        )
        self._request_headers = {
            name: headers.get(name, "") for name in names
        }

    def _fetch_shop_switch_accounts(self, page: Any) -> list[dict[str, Any]]:
        response = self._request_json(
            page,
            "GET",
            "/shop-faas/mmecnodelogin/session/getShopSwitchList"
            "?token=&lang=zh_CN",
        )
        if int(response.get("status") or 0) >= 400:
            return []
        return extract_shop_switch_accounts(response.get("payload"))

    @staticmethod
    def _accept_multi_account_login(page: Any) -> bool:
        """Use the official multi-login selector and choose every account."""
        try:
            frames = list(page.frames)
        except Exception:
            frames = []
        if page not in frames:
            frames.insert(0, page)

        selection_roots: list[Any] = []
        for target in frames:
            try:
                body_text = target.locator("body").inner_text(timeout=900)
            except Exception:
                continue
            if (
                "选择要登录的账号" in body_text
                or "选择账号登录" in body_text
                or "选择要登录的店铺" in body_text
            ):
                selection_roots.append(target)
        if not selection_roots:
            return False

        for target in selection_roots:
            selected_all = False
            for locator_factory in (
                lambda item: item.get_by_role(
                    "checkbox",
                    name="全选",
                    exact=True,
                ).first,
                lambda item: item.get_by_text("全选", exact=True).first,
            ):
                try:
                    select_all = locator_factory(target)
                    if (
                        select_all.count()
                        and select_all.is_visible()
                        and select_all.is_enabled()
                    ):
                        select_all.click()
                        target.wait_for_timeout(550)
                        selected_all = True
                        break
                except Exception:
                    continue
            if not selected_all:
                continue

            for locator_factory in (
                lambda item: item.get_by_role(
                    "button",
                    name="登录",
                    exact=True,
                ).last,
                lambda item: item.get_by_text("登录", exact=True).last,
            ):
                try:
                    login_button = locator_factory(target)
                    if (
                        login_button.count()
                        and login_button.is_visible()
                        and login_button.is_enabled()
                    ):
                        login_button.click()
                        target.wait_for_timeout(350)
                        return True
                except Exception:
                    continue
        return False

    def _switch_shop(self, page: Any, app_id: str) -> bool:
        response = self._request_json(
            page,
            "POST",
            "/shop-faas/mmecnodelogin/session/switchShop",
            {"appid": app_id},
        )
        if int(response.get("status") or 0) >= 400:
            return False
        payload = response.get("payload")
        if not isinstance(payload, dict):
            return False
        try:
            return int(payload.get("code") or 0) == 0
        except (TypeError, ValueError):
            return False

    def _capture_all_shop_logins(
        self,
        page: Any,
        context: Any,
        progress: Callable[[str, int, int], None] | None = None,
        allowed_app_ids: set[str] | None = None,
        *,
        on_store_saved: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Enumerate and persist every shop visible to the same WeChat login."""
        available_accounts = self._fetch_shop_switch_accounts(page)
        switch_accounts = filter_shop_switch_accounts(
            available_accounts,
            allowed_app_ids,
        )
        if not switch_accounts:
            return {
                "matched_store_ids": [],
                "discovered_count": 0,
                "available_count": len(available_accounts),
                "failures": [],
            }
        captured: list[dict[str, Any]] = []
        failures: list[str] = []
        created_ids: list[int] = []
        current_state = self._page_initial_state(page)
        current_app_id = str(
            (current_state.get("user_info") or {}).get("appid")
            or ""
        )
        for account_index, item in enumerate(switch_accounts, 1):
            app_id = str(
                item.get("appid") or item.get("appId") or ""
            ).strip()
            if not app_id:
                continue
            if progress:
                progress(
                    f"正在切换并登录 "
                    f"{account_index}/{len(switch_accounts)}",
                    account_index - 1,
                    len(switch_accounts),
                )
            account_state = current_state
            if current_app_id != app_id:
                if not self._switch_shop(page, app_id):
                    failures.append(
                        str(item.get("nickname") or app_id)
                    )
                    continue
                account_state = {}
                for _attempt in range(8):
                    refreshed_state = self._page_initial_state(page)
                    refreshed_app_id = str(
                        (refreshed_state.get("user_info") or {}).get(
                            "appid"
                        )
                        or ""
                    )
                    if refreshed_app_id == app_id:
                        account_state = refreshed_state
                        break
                    page.wait_for_timeout(180)
                if not account_state:
                    # Only use the slower full-page fallback when the fast
                    # session-state poll did not confirm the switch.
                    try:
                        page.goto(
                            WORKBENCH_URL,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                    except Exception as exc:
                        failures.append(
                            f"{item.get('nickname') or app_id}: {exc}"
                        )
                        continue
                    page.wait_for_timeout(500)
                    for _attempt in range(8):
                        refreshed_state = self._page_initial_state(page)
                        refreshed_app_id = str(
                            (refreshed_state.get("user_info") or {}).get(
                                "appid"
                            )
                            or ""
                        )
                        if refreshed_app_id == app_id:
                            account_state = refreshed_state
                            break
                        page.wait_for_timeout(500)
                if not account_state:
                    failures.append(
                        f"{item.get('nickname') or app_id}: "
                        "切换后未确认店铺身份"
                    )
                    continue
            current_app_id = app_id
            account = normalize_shop_switch_account(
                item,
                account_state,
            )
            if account is None:
                failures.append(
                    f"{item.get('nickname') or app_id}: "
                    "店铺身份校验失败"
                )
                continue
            cookies = normalize_cookies(context.cookies())
            if not cookies:
                failures.append(
                    f"{account['name']}: 没有可保存的登录 Cookie"
                )
                continue
            store_id, created = self.db.ensure_web_login_store(
                str(account.get("name") or ""),
                str(account.get("app_id") or ""),
            )
            if created and store_id not in created_ids:
                created_ids.append(store_id)
            profile_info = {
                "nickname": str(account.get("name") or ""),
                "headimg_url": str(account.get("headimg_url") or ""),
                "subject_type": "",
                "status": "登录态有效",
                "username": str(account.get("store_username") or ""),
                "is_local_life": 0,
                "open_timestamp": 0,
                "wechat_account": str(
                    account.get("account_username")
                    or account.get("account_nickname")
                    or ""
                ),
                "account_role": str(
                    account.get("account_role") or "unknown"
                ),
                "ec_acct_logic_type": account.get("ec_acct_logic_type"),
                "shop_type": account.get("shop_type"),
            }
            self.db.upsert_store_profile(store_id, profile_info)
            payload = {
                "cookies": list(cookies),
                "source_url": str(page.url or WORKBENCH_URL),
                "saved_at": int(time.time()),
                "login_confirmed": True,
                "matched_store_id": store_id,
                "matched_store_ids": [store_id],
                "matched_store_name": account.get("name") or "",
                "matched_store_names": [
                    account.get("name") or ""
                ],
                "wechat_account": profile_info["wechat_account"],
                "account_role": profile_info["account_role"],
                "shop_switch_login": True,
            }
            self.db.set_web_session(
                store_id,
                payload,
                "已登录",
                "微信扫码逐店登录成功",
            )
            if on_store_saved is not None:
                try:
                    on_store_saved(
                        store_id,
                        str(account.get("name") or ""),
                    )
                except Exception as exc:
                    self._log(
                        "store saved callback failed "
                        f"store={store_id} "
                        f"error={type(exc).__name__}"
                    )
            if progress:
                progress(
                    f"已登录并保存 {store_id}："
                    f"{account.get('name') or ''}",
                    account_index,
                    len(switch_accounts),
                )
            captured.append(
                {
                    **account,
                    "cookies": cookies,
                    "source_url": str(page.url or WORKBENCH_URL),
                    "_store_id": store_id,
                    "_profile_info": profile_info,
                }
            )
        selected, excluded_main = select_preferred_shop_accounts(captured)
        if not selected:
            return {
                "matched_store_ids": [],
                "failures": failures,
                "discovered_count": len(switch_accounts),
            }

        selected_ids: list[int] = []
        matched_names: list[str] = []
        common_accounts = {
            (
                account.get("account_username")
                or account.get("account_nickname")
            )
            for account in captured
        }
        common_accounts.discard("")
        wechat_account = next(iter(common_accounts), "")
        for account in selected:
            store_id = int(account.get("_store_id") or 0)
            if store_id and store_id not in selected_ids:
                selected_ids.append(store_id)
            matched_names.append(str(account.get("name") or ""))
        excluded_main_ids: list[int] = []
        for account in excluded_main:
            store_id = int(account.get("_store_id") or 0)
            if not store_id:
                continue
            excluded_main_ids.append(store_id)
            self.db.clear_web_session(
                store_id,
                detail="同一微信存在子账号，已按规则保留子账号登录态",
            )
            if on_store_saved is not None:
                try:
                    on_store_saved(
                        store_id,
                        str(account.get("name") or ""),
                    )
                except Exception as exc:
                    self._log(
                        "store cleared callback failed "
                        f"store={store_id} error={type(exc).__name__}"
                    )
        return {
            "matched_store_ids": selected_ids,
            "matched_store_names": matched_names,
            "created_store_ids": created_ids,
            "excluded_main_names": [
                str(account.get("name") or "")
                for account in excluded_main
            ],
            "excluded_main_ids": excluded_main_ids,
            "wechat_account": wechat_account,
            "discovered_count": len(switch_accounts),
            "available_count": len(available_accounts),
            "failures": failures,
        }

    def _capture_all_shop_logins_with_retries(
        self,
        page: Any,
        context: Any,
        progress: Callable[[str, int, int], None] | None = None,
        allowed_app_ids: set[str] | None = None,
        attempts: int = 6,
        delay_ms: int = 1200,
        on_store_saved: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Wait for the official switch list to become available."""
        last_result: dict[str, Any] = {}
        for attempt in range(1, max(1, attempts) + 1):
            try:
                result = self._capture_all_shop_logins(
                    page,
                    context,
                    progress,
                    allowed_app_ids,
                    on_store_saved=on_store_saved,
                )
            except Exception as exc:
                result = {}
                self._log(
                    "multi shop capture retry failed "
                    f"attempt={attempt} error={type(exc).__name__}"
                )
            last_result = result if isinstance(result, dict) else {}
            discovered = int(last_result.get("discovered_count") or 0)
            matched = len(last_result.get("matched_store_ids") or [])
            excluded = len(last_result.get("excluded_main_ids") or [])
            self._log(
                "multi shop capture attempt="
                f"{attempt} discovered={discovered} matched={matched} "
                f"excluded_main={excluded}"
            )
            if matched and (
                discovered <= 0
                or matched + excluded >= discovered
            ):
                return last_result
            if attempt < attempts:
                try:
                    if page.is_closed():
                        break
                    page.wait_for_timeout(max(100, delay_ms))
                except Exception:
                    break
        return last_result

    def interactive_login(
        self,
        store: Store,
        progress: Callable[[str, int, int], None] | None = None,
        allowed_store_ids: set[int] | None = None,
        on_store_saved: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        PlaywrightError, sync_playwright = playwright_api()

        storage_path = self.data_dir / "edge_profiles" / f"store_{store.id}"
        storage_path.mkdir(parents=True, exist_ok=True)
        captured_cookies: list[dict[str, Any]] = []
        current_url = SHOP_HOME
        login_confirmed = False
        multi_login_result: dict[str, Any] = {}
        multi_login_requested = False
        matched_store = store
        identity = self._store_identity(store)
        identities = self.db.list_store_identities()
        local_stores = self.db.list_stores()
        allowed_app_ids = {
            str(item.app_id).strip()
            for item in local_stores
            if str(item.app_id).strip()
            and (
                allowed_store_ids is None
                or item.id in allowed_store_ids
            )
        }
        if not any(
            int(item.get("store_id") or 0) == store.id
            for item in identities
        ):
            identities.append(identity)
        playwright = sync_playwright().start()
        context = None
        self._request_headers = {}
        self._log(f"login start store={store.id} name={store.name}")
        try:
            context = playwright.chromium.launch_persistent_context(
                str(storage_path),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--start-maximized",
                        "--no-first-run",
                        "--disable-session-crashed-bubble",
                    ],
                ),
            )
            context.on(
                "request",
                self._capture_official_request_headers,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                MULTI_LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            # A persistent browser profile can silently restore a different
            # shop's session. Clear it so the QR code always belongs to the
            # store the user selected.
            try:
                page.evaluate(
                    "() => { localStorage.clear(); sessionStorage.clear(); }"
                )
            except PlaywrightError:
                pass
            context.clear_cookies()
            page.goto(
                MULTI_LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            deadline = time.time() + 300
            next_cookie_probe = 0.0
            while time.time() < deadline:
                try:
                    cookies = normalize_cookies(context.cookies())
                    if cookies:
                        captured_cookies = cookies
                    for open_page in context.pages:
                        if open_page.is_closed():
                            continue
                        candidate_url = open_page.url
                        if not candidate_url.startswith("http"):
                            continue
                        current_url = candidate_url
                        body_text = open_page.locator("body").inner_text(timeout=1200)
                        if self._accept_multi_account_login(open_page):
                            multi_login_requested = True
                            time.sleep(2.0)
                            body_text = open_page.locator(
                                "body"
                            ).inner_text(timeout=1200)
                        login_page = "扫码进入我的小店" in body_text
                        if (
                            not login_page
                            and urlsplit(candidate_url).hostname == "store.weixin.qq.com"
                            and len(captured_cookies) >= 2
                        ):
                            identified_id = 0
                            if time.time() >= next_cookie_probe:
                                next_cookie_probe = time.time() + 8.0
                                begin_batch = getattr(
                                    self.db,
                                    "begin_store_login_batch",
                                    None,
                                )
                                end_batch = getattr(
                                    self.db,
                                    "end_store_login_batch",
                                    None,
                                )
                                if callable(begin_batch):
                                    begin_batch()
                                try:
                                    try:
                                        switch_result = (
                                            self._capture_all_shop_logins_with_retries(
                                                open_page,
                                                context,
                                                progress,
                                                allowed_app_ids,
                                                on_store_saved=on_store_saved,
                                            )
                                        )
                                    except Exception as exc:
                                        self._log(
                                            "shop switch enumeration failed "
                                            f"store={store.id} "
                                            f"error={type(exc).__name__}"
                                        )
                                        switch_result = {}
                                finally:
                                    if callable(end_batch):
                                        end_batch()
                                if switch_result.get(
                                    "matched_store_ids"
                                ):
                                    multi_login_result = switch_result
                                    login_confirmed = True
                                    matched_store = (
                                        self.db.get_store(
                                            int(
                                                switch_result[
                                                    "matched_store_ids"
                                                ][0]
                                            )
                                        )
                                        or store
                                    )
                                    break
                                if multi_login_requested:
                                    self._log(
                                        "multi login selected but shop switch "
                                        "list is not ready "
                                        f"store={store.id}"
                                    )
                                    if progress:
                                        progress(
                                            "已选择多账号，正在等待微信返回"
                                            "全部店铺登录态",
                                            0,
                                            0,
                                        )
                                    continue
                                identified_id, _identified_name = (
                                    self._identify_logged_in_store(
                                        captured_cookies,
                                        identities,
                                    )
                                )
                            if not identified_id:
                                body_matches = matching_identities(
                                    body_text,
                                    identities,
                                )
                                if len(body_matches) == 1:
                                    identified_id = int(
                                        body_matches[0].get("store_id") or 0
                                    )
                            if identified_id:
                                actual_store = self.db.get_store(identified_id)
                                if actual_store is None:
                                    raise WebSessionError(
                                        "已识别登录账号，但本地没有对应店铺记录"
                                    )
                                matched_store = actual_store
                                login_confirmed = True
                                break
                    if login_confirmed:
                        self._log(
                            f"login confirmed store={store.id} "
                            f"cookies={len(captured_cookies)} path={urlsplit(current_url).path}"
                        )
                        break
                except PlaywrightError as exc:
                    self._log(
                        f"login browser closed store={store.id} "
                        f"cookies={len(captured_cookies)} error={type(exc).__name__}"
                    )
                    if captured_cookies:
                        break
                    raise WebSessionError("登录窗口意外关闭")
                time.sleep(0.8)
        finally:
            if context is not None:
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        captured_cookies = latest
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        cookies = captured_cookies
        if not cookies:
            self._log(f"login failed store={store.id} no cookies")
            raise WebSessionError("没有捕获到登录 Cookie，请重新扫码并停留在小店后台")
        if not login_confirmed:
            self._log(f"login rejected store={store.id} identity unconfirmed")
            raise WebSessionError(
                "已捕获登录信息，但无法确认扫码店铺身份；"
                "为避免串店，登录状态未保存"
            )
        if multi_login_result:
            matched_ids = [
                int(value)
                for value in multi_login_result.get(
                    "matched_store_ids", []
                )
            ]
            self._log(
                f"shop switch login saved requested={store.id} "
                f"stores={len(matched_ids)} visible="
                f"{multi_login_result.get('available_count', 0)} "
                f"targets="
                f"{multi_login_result.get('discovered_count', 0)} "
                f"excluded_main="
                f"{len(multi_login_result.get('excluded_main_names', []))}"
            )
            return {
                "cookies": list(captured_cookies),
                "source_url": current_url or SHOP_HOME,
                "saved_at": int(time.time()),
                "login_confirmed": True,
                "matched_store_id": matched_ids[0] if matched_ids else None,
                "matched_store_ids": matched_ids,
                "matched_store_name": (
                    multi_login_result.get("matched_store_names") or [""]
                )[0],
                "matched_store_names": list(
                    multi_login_result.get("matched_store_names") or []
                ),
                "created_store_ids": list(
                    multi_login_result.get("created_store_ids") or []
                ),
                "excluded_main_names": list(
                    multi_login_result.get("excluded_main_names") or []
                ),
                "excluded_main_ids": list(
                    multi_login_result.get("excluded_main_ids") or []
                ),
                "wechat_account": str(
                    multi_login_result.get("wechat_account") or ""
                ),
                "shop_switch_count": int(
                    multi_login_result.get("discovered_count") or 0
                ),
                "shop_switch_login": True,
                "failures": list(
                    multi_login_result.get("failures") or []
                ),
            }
        payload = {
            "cookies": cookies,
            "source_url": current_url or SHOP_HOME,
            "saved_at": int(time.time()),
            "login_confirmed": login_confirmed,
        }
        status, detail = self.probe_payload(
            payload,
            store=matched_store,
            require_identity=True,
        )
        if status != "已登录":
            self._log(
                f"login rejected store={store.id} status={status}"
            )
            raise WebSessionError(
                f"已捕获 Cookie，但店铺身份校验失败：{status}（{detail}）"
            )
        self.db.set_web_session(matched_store.id, payload, status, detail)
        payload["matched_store_id"] = matched_store.id
        payload["matched_store_name"] = matched_store.name
        self._log(
            f"login saved store={matched_store.id} requested={store.id} "
            f"cookies={len(cookies)} "
            f"status={status} confirmed={login_confirmed}"
        )
        return payload

    def import_cookie_header(self, store: Store, header: str) -> dict[str, Any]:
        cookies = parse_cookie_header(header)
        payload = {
            "cookies": cookies,
            "source_url": SHOP_HOME,
            "saved_at": int(time.time()),
        }
        status, detail = self.probe_payload(
            payload,
            store=store,
            require_identity=True,
        )
        if status != "已登录":
            raise WebSessionError(
                f"Cookie 未保存，店铺身份校验失败：{status}（{detail}）"
            )
        self.db.set_web_session(store.id, payload, status, detail)
        return payload

    def bulk_register_activity_products(
        self,
        store: Store,
        activity_id: int,
        template_path: Path,
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        PlaywrightError, sync_playwright = playwright_api()
        payload = self.db.get_web_cookies(store.id)
        if not payload:
            raise WebSessionError("店铺网页未登录，请先扫码登录")
        cookies = []
        for cookie in normalize_cookies(payload.get("cookies") or []):
            item = {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": "store.weixin.qq.com",
                "path": cookie.get("path") or "/",
            }
            try:
                expires = float(cookie.get("expires") or 0)
            except (TypeError, ValueError):
                expires = 0
            if expires > 0:
                item["expires"] = expires
            cookies.append(item)
        response_result: dict[str, Any] = {}
        playwright = sync_playwright().start()
        context = None
        try:
            storage_path = (
                self.data_dir / "edge_profiles" / f"store_{store.id}"
            )
            storage_path.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(storage_path),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1440, "height": 900},
                ),
            )
            context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()

            def capture_response(response) -> None:
                if "updateRequestProductList" not in response.url:
                    return
                try:
                    response_result.update(response.json())
                except Exception:
                    response_result["error"] = response.text()[:1000]

            page.on("response", capture_response)
            url = (
                "https://store.weixin.qq.com/shop/marketingkol/"
                f"activity/detail?id={int(activity_id)}"
                "&goToUploadProduct=1"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)

            # The activity page opens a "一键预填" modal before the normal
            # product-import entry can be used. It covers the whole page and
            # intercepts clicks, so explicitly cancel it.
            prefill_dialog = (
                page.locator(".weui-desktop-dialog:visible")
                .filter(has_text="一键预填")
                .last
            )
            if prefill_dialog.count():
                cancel_prefill = prefill_dialog.get_by_role(
                    "button",
                    name="取消",
                    exact=True,
                )
                if cancel_prefill.count():
                    cancel_prefill.click()
                    page.wait_for_timeout(700)

            for label in ("我知道了", "知道了", "关闭"):
                for notice in page.get_by_text(label, exact=True).all():
                    try:
                        if notice.is_visible():
                            notice.click()
                            page.wait_for_timeout(300)
                    except PlaywrightError:
                        continue
            add_button = None
            for element in page.get_by_text("添加", exact=True).all():
                if element.is_visible():
                    add_button = element
                    break
            if add_button is None:
                raise WebSessionError("活动页面未找到“添加”入口")
            add_button.click()
            page.wait_for_timeout(1500)
            bulk_entry = page.get_by_text(
                "批量导入商品",
                exact=False,
            ).filter(visible=False).first
            if bulk_entry.count() == 0:
                raise WebSessionError("活动页面未找到“批量导入商品”入口")
            bulk_entry.evaluate("element => element.click()")
            bulk_dialog = (
                page.locator(".weui-desktop-dialog:visible")
                .filter(has_text="批量导入")
                .last
            )
            deadline = time.time() + 15
            while time.time() < deadline and bulk_dialog.count() == 0:
                page.wait_for_timeout(300)
                bulk_dialog = (
                    page.locator(".weui-desktop-dialog:visible")
                    .filter(has_text="批量导入")
                    .last
                )
            if bulk_dialog.count() == 0:
                raise WebSessionError("平台未打开“批量导入”窗口")
            file_input = page.locator("input[type=file]").nth(0)
            if file_input.count() == 0:
                raise WebSessionError("活动页面未找到批量导入文件框")
            file_input.set_input_files(str(template_path))

            # The file picker reports completion before the backend has
            # finished resolving SPU/SKU rows. Clicking "确认" during that
            # window leaves the import dialog open with no result.
            parsed_text = ""
            deadline = time.time() + 45
            confirm = bulk_dialog.get_by_role("button", name="确认")
            while time.time() < deadline:
                parsed_text = (
                    bulk_dialog.inner_text(timeout=2000)
                    if bulk_dialog.count()
                    else ""
                )
                if (
                    "共识别到" in parsed_text
                    and "加载中" not in parsed_text
                    and confirm.count()
                    and confirm.is_enabled()
                ):
                    break
                page.wait_for_timeout(500)
            if (
                "共识别到" not in parsed_text
                or "加载中" in parsed_text
                or confirm.count() == 0
                or not confirm.is_enabled()
            ):
                raise WebSessionError("平台未通过批量模板解析")
            recognized_match = re.search(r"共识别到\s*(\d+)\s*个商品", parsed_text)
            recognized_products = (
                int(recognized_match.group(1))
                if recognized_match
                else None
            )
            confirm.click()

            report_dialog = None
            deadline = time.time() + 60
            while time.time() < deadline:
                candidate = (
                    page.locator(".weui-desktop-dialog:visible")
                    .filter(has_text="提报商品")
                    .last
                )
                if candidate.count():
                    report_dialog = candidate
                    break
                page.wait_for_timeout(500)
            if report_dialog is None or report_dialog.count() == 0:
                raise WebSessionError("平台未进入提报商品确认页")

            report_text = ""
            deadline = time.time() + 30
            while time.time() < deadline:
                report_text = report_dialog.inner_text(timeout=3000)
                if "已批量导入上传" in report_text:
                    break
                page.wait_for_timeout(500)
            imported_match = re.search(
                r"已批量导入上传\s*(\d+)\s*个sku，"
                r"其中\s*(\d+)\s*个sku可提报",
                report_text,
                re.IGNORECASE,
            )
            imported_count = (
                int(imported_match.group(1))
                if imported_match
                else int(expected_count or 0)
            )
            valid_count = (
                int(imported_match.group(2))
                if imported_match
                else int(expected_count or 0)
            )
            if valid_count <= 0:
                summary = report_text.split("下载执行明细", 1)[0].strip()
                raise WebSessionError(
                    summary or "平台批量导入后没有可提报商品"
                )

            agreement = report_dialog.locator(
                ".weui-desktop-dialog__ft input[type=checkbox]"
            )
            deadline = time.time() + 15
            while time.time() < deadline and agreement.count() == 0:
                page.wait_for_timeout(300)
                agreement = report_dialog.locator(
                    ".weui-desktop-dialog__ft input[type=checkbox]"
                )
            if agreement.count() == 0:
                raise WebSessionError("平台确认页缺少活动协议勾选框")
            agreement.evaluate("element => element.click()")
            submit = (
                report_dialog.locator(".weui-desktop-dialog__ft button")
                .filter(has_text="确定")
                .first
            )
            deadline = time.time() + 10
            while time.time() < deadline and (
                submit.count() == 0 or not submit.is_enabled()
            ):
                page.wait_for_timeout(300)
            if submit.count() == 0 or not submit.is_enabled():
                raise WebSessionError("活动协议未成功勾选")
            submit.click()
            deadline = time.time() + 30
            while time.time() < deadline and not response_result:
                page.wait_for_timeout(500)
        except PlaywrightError as exc:
            raise WebSessionError(
                f"平台批量活动导入失败：{exc}"
            ) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        if not response_result:
            raise WebSessionError("平台没有返回批量商品提交结果")
        if response_result.get("code") not in (None, 0, "0"):
            raise WebSessionError(
                str(
                    response_result.get("errmsg")
                    or response_result.get("message")
                    or "平台批量商品提交失败"
                )
            )
        invalid = [
            row
            for row in response_result.get("checkResultList") or []
            if isinstance(row, dict)
            and (
                str(row.get("invalidString") or "").strip()
                or str(row.get("invalidStringPrecheck") or "").strip()
            )
        ]
        if invalid:
            raise WebSessionError(
                f"平台有 {len(invalid)} 个SKU校验失败"
            )
        result_count = len(response_result.get("checkResultList") or [])
        return {
            "activity_id": int(activity_id),
            "store_id": store.id,
            "count": result_count,
            "expected_count": int(expected_count or 0),
            "recognized_products": recognized_products,
            "imported_count": imported_count,
            "valid_count": valid_count,
            "skipped_count": max(0, imported_count - valid_count),
            "shortfall": max(
                0,
                int(expected_count or imported_count) - result_count,
            ),
            "result": response_result,
        }

    def apply_platform_activity_page(
        self,
        store: Store,
        activity_id: int,
    ) -> dict[str, Any]:
        PlaywrightError, sync_playwright = playwright_api()
        payload = self.db.get_web_cookies(store.id)
        if not payload:
            raise WebSessionError("店铺网页未登录，请先扫码登录")
        cookies = []
        for cookie in normalize_cookies(payload.get("cookies") or []):
            item = {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": "store.weixin.qq.com",
                "path": cookie.get("path") or "/",
            }
            try:
                expires = float(cookie.get("expires") or 0)
            except (TypeError, ValueError):
                expires = 0
            if expires > 0:
                item["expires"] = expires
            cookies.append(item)
        responses: list[dict[str, Any]] = []
        opened_pages = []
        playwright = sync_playwright().start()
        context = None
        try:
            storage_path = (
                self.data_dir / "edge_profiles" / f"store_{store.id}"
            )
            storage_path.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(storage_path),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1440, "height": 900},
                ),
            )
            context.add_cookies(cookies)
            context.on("page", lambda new_page: opened_pages.append(new_page))
            page = context.pages[0] if context.pages else context.new_page()

            def capture_response(response) -> None:
                if "applyShopPlatformActivity" not in response.url:
                    return
                try:
                    result = response.json()
                except Exception:
                    return
                if isinstance(result, dict):
                    responses.append(result)

            page.on("response", capture_response)
            page.goto(
                "https://store.weixin.qq.com/shop/marketingkol/"
                f"activity/detail?id={int(activity_id)}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(5000)
            body_text = page.locator("body").inner_text(timeout=5000)
            if any(
                marker in body_text
                for marker in ("已报名", "已参与", "取消报名")
            ):
                return {
                    "activity_id": int(activity_id),
                    "store_id": store.id,
                    "status": "already_registered",
                    "message": "原平台显示该店铺已经报名",
                }
            apply_button = page.get_by_role(
                "button",
                name="报名",
                exact=True,
            ).first
            if apply_button.count() == 0:
                apply_button = page.get_by_text(
                    "报名",
                    exact=True,
                ).first
            if apply_button.count() == 0 or not apply_button.is_visible():
                report_button = page.get_by_role(
                    "button",
                    name="提报商品",
                    exact=True,
                ).first
                if report_button.count() and report_button.is_visible():
                    return {
                        "activity_id": int(activity_id),
                        "store_id": store.id,
                        "status": "already_registered",
                        "message": "原平台已进入提报商品页面，店铺已经报名",
                    }
                raise WebSessionError(
                    "原活动页没有可用的“报名”按钮，活动可能结束或受限"
                )
            apply_button.click()
            page.wait_for_timeout(5000)
            if responses:
                result = responses[-1]
                if result.get("code") not in (None, 0, "0"):
                    raise WebSessionError(
                        str(
                            result.get("errmsg")
                            or result.get("errMsg")
                            or result.get("message")
                            or "原平台报名失败"
                        )
                    )
                return {
                    "activity_id": int(activity_id),
                    "store_id": store.id,
                    "status": "submitted",
                    "message": "已在原平台完成报名",
                    "result": result,
                }
            new_page = next(
                (
                    candidate
                    for candidate in opened_pages
                    if not candidate.is_closed()
                ),
                None,
            )
            if new_page is not None:
                new_page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=20000,
                )
                new_page.wait_for_timeout(6000)
                required_fields = [
                    label
                    for label in (
                        "视频号",
                        "开播日期",
                        "目标GMV",
                        "手机号",
                        "微信号",
                    )
                    if new_page.get_by_text(label, exact=False).count()
                ]
                if required_fields:
                    raise WebSessionError(
                        "原平台报名表单需要补充"
                        + "、".join(required_fields)
                        + "；软件未替店铺填写或提交"
                    )
                raise WebSessionError(
                    "原平台打开了报名表，但需要补充活动信息"
                )
            dialogs = page.locator(".weui-desktop-dialog:visible")
            if dialogs.count():
                dialog_text = dialogs.last.inner_text(timeout=3000)
                raise WebSessionError(
                    "原平台弹出了报名确认，但无法安全自动完成："
                    + dialog_text[:200]
                )
            raise WebSessionError("原平台没有返回报名结果")
        except PlaywrightError as exc:
            raise WebSessionError(
                f"原平台活动报名失败：{exc}"
            ) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()

    def renew_snapup_activity(
        self,
        store: Store,
        activity_id: str,
        start_time: int,
        days: int = 7,
    ) -> dict[str, Any]:
        PlaywrightError, sync_playwright = playwright_api()
        payload = self.db.get_web_cookies(store.id)
        if not payload:
            raise WebSessionError("店铺网页未登录，请先扫码登录")
        cookies = []
        for cookie in normalize_cookies(payload.get("cookies") or []):
            item = {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": "store.weixin.qq.com",
                "path": cookie.get("path") or "/",
            }
            try:
                expires = float(cookie.get("expires") or 0)
            except (TypeError, ValueError):
                expires = 0
            if expires > 0:
                item["expires"] = expires
            cookies.append(item)
        start = datetime.fromtimestamp(
            max(int(time.time()) + 60, start_time),
            SHANGHAI_TZ,
        )
        end = start + timedelta(days=max(1, int(days)))
        start_text = start.strftime("%Y-%m-%d %H:%M:%S")
        end_text = end.strftime("%Y-%m-%d %H:%M:%S")
        responses: list[dict[str, Any]] = []
        playwright = sync_playwright().start()
        context = None
        try:
            storage_path = (
                self.data_dir / "edge_profiles" / f"store_{store.id}"
            )
            storage_path.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(storage_path),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1440, "height": 900},
                ),
            )
            context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()

            def capture_response(response) -> None:
                if "createSnapUpTaskV2" not in response.url:
                    return
                try:
                    result = response.json()
                except Exception:
                    return
                if isinstance(result, dict):
                    responses.append(result)

            page.on("response", capture_response)
            page.goto(
                "https://store.weixin.qq.com/shop/marketing/snapup/create"
                f"?snapupId={activity_id}&copy=1",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(6500)
            custom = page.get_by_text("自定义", exact=True).first
            if custom.count() and custom.is_visible():
                custom.click()
                page.wait_for_timeout(400)
            for label, value in (
                ("开始日期", start_text),
                ("结束日期", end_text),
            ):
                field = page.locator(
                    f'input[placeholder="{label}"]'
                ).first
                if field.count() == 0 or not field.is_visible():
                    raise WebSessionError(
                        f"原平台限时抢购表单未找到“{label}”"
                    )
                field.click()
                page.keyboard.press("Control+A")
                page.keyboard.type(value, delay=15)
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)
                page.keyboard.press("Escape")
            name_field = page.locator(
                'input[placeholder="输入活动名称"]'
            ).first
            if name_field.count():
                name_field.fill(
                    f"限时{days}天 "
                    f"{start.strftime('%m/%d')}至{end.strftime('%m/%d')}"
                )
            confirm = page.get_by_role(
                "button",
                name="确定",
                exact=True,
            ).first
            if confirm.count() == 0 or not confirm.is_enabled():
                raise WebSessionError("原平台限时抢购无法提交")
            confirm.click()
            deadline = time.time() + 30
            while time.time() < deadline and not responses:
                page.wait_for_timeout(500)
            if not responses:
                errors = []
                for message in (
                    "请选择活动商品",
                    "活动商品",
                    "活动时间",
                    "活动名称",
                    "保存失败",
                    "创建失败",
                ):
                    if page.get_by_text(message, exact=False).count():
                        errors.append(message)
                if errors:
                    raise WebSessionError(
                        "原平台未创建续期活动："
                        + "；".join(dict.fromkeys(errors))
                    )
                raise WebSessionError("原平台没有返回限时抢购创建结果")
        except PlaywrightError as exc:
            raise WebSessionError(
                f"原平台限时抢购续期失败：{exc}"
            ) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        result = responses[-1]
        if result.get("code") not in (None, 0, "0"):
            raise WebSessionError(
                str(
                    result.get("errmsg")
                    or result.get("errMsg")
                    or result.get("message")
                    or "原平台限时抢购创建失败"
                )
            )
        def response_activity_id(value: Any) -> str:
            if isinstance(value, dict):
                for key in (
                    "newActivityId",
                    "new_activity_id",
                    "snapupTaskId",
                    "activityId",
                    "activity_id",
                ):
                    candidate = str(value.get(key) or "").strip()
                    if candidate and candidate != str(activity_id):
                        return candidate
                for item in value.values():
                    candidate = response_activity_id(item)
                    if candidate:
                        return candidate
            elif isinstance(value, list):
                for item in value:
                    candidate = response_activity_id(item)
                    if candidate:
                        return candidate
            return ""

        new_activity_id = response_activity_id(result)
        return {
            "status": "created",
            "activity_id": new_activity_id or str(activity_id),
            "source_activity_id": str(activity_id),
            "new_activity_id": new_activity_id,
            "store_id": store.id,
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "days": int(days),
            "result": result,
        }

    def renew_coupon(
        self,
        store: Store,
        coupon_id: str,
        coupon_type: int,
        promote_type: int,
        start_time: int,
        days: int = 7,
    ) -> dict[str, Any]:
        PlaywrightError, sync_playwright = playwright_api()
        payload = self.db.get_web_cookies(store.id)
        if not payload:
            raise WebSessionError("店铺网页未登录，请先扫码登录")
        cookies = []
        for cookie in normalize_cookies(payload.get("cookies") or []):
            item = {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": "store.weixin.qq.com",
                "path": cookie.get("path") or "/",
            }
            try:
                expires = float(cookie.get("expires") or 0)
            except (TypeError, ValueError):
                expires = 0
            if expires > 0:
                item["expires"] = expires
            cookies.append(item)
        start = datetime.fromtimestamp(
            max(int(time.time()) + 60, start_time),
            SHANGHAI_TZ,
        )
        end = start + timedelta(days=max(1, int(days)))
        start_text = start.strftime("%Y-%m-%d %H:%M:%S")
        end_text = end.strftime("%Y-%m-%d %H:%M:%S")
        responses: list[dict[str, Any]] = []
        playwright = sync_playwright().start()
        context = None
        try:
            storage_path = (
                self.data_dir / "edge_profiles" / f"store_{store.id}"
            )
            storage_path.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                str(storage_path),
                **browser_launch_options(
                    headless=True,
                    viewport={"width": 1440, "height": 900},
                ),
            )
            context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()

            def capture_response(response) -> None:
                if "/market/coupon/cgi/makeQp" not in response.url:
                    return
                try:
                    result = response.json()
                except Exception:
                    return
                if isinstance(result, dict):
                    responses.append(result)

            page.on("response", capture_response)
            page.goto(
                "https://store.weixin.qq.com/shop/marketing/coupon/make"
                f"?copy=1&type={int(coupon_type)}"
                f"&originType={int(coupon_type)}"
                f"&promoteType={int(promote_type)}"
                f"&couponId={coupon_id}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(6500)
            for label, value in (
                ("开始日期", start_text),
                ("结束日期", end_text),
            ):
                field = page.locator(
                    f'input[placeholder="{label}"]'
                ).first
                if field.count() == 0 or not field.is_visible():
                    raise WebSessionError(
                        f"原平台优惠券表单未找到“{label}”"
                    )
                field.click()
                page.keyboard.press("Control+A")
                page.keyboard.type(value, delay=15)
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)
                page.keyboard.press("Escape")
            create = page.get_by_role(
                "button",
                name="创建",
                exact=True,
            ).first
            if create.count() == 0 or not create.is_enabled():
                raise WebSessionError("原平台优惠券无法创建续期券")
            create.click()
            deadline = time.time() + 30
            while time.time() < deadline and not responses:
                page.wait_for_timeout(500)
            if not responses:
                errors = []
                for message in (
                    "领取时间",
                    "优惠券名称",
                    "创建失败",
                    "保存失败",
                ):
                    if page.get_by_text(message, exact=False).count():
                        errors.append(message)
                if errors:
                    raise WebSessionError(
                        "原平台未创建续期优惠券："
                        + "；".join(dict.fromkeys(errors))
                    )
                raise WebSessionError("原平台没有返回优惠券创建结果")
        except PlaywrightError as exc:
            raise WebSessionError(
                f"原平台优惠券续期失败：{exc}"
            ) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()
        result = responses[-1]
        if result.get("code") not in (None, 0, "0"):
            raise WebSessionError(
                str(
                    result.get("errmsg")
                    or result.get("errMsg")
                    or result.get("message")
                    or "原平台优惠券创建失败"
                )
            )
        coupon = result.get("coupon") or result.get("data") or {}
        coupon_key = coupon.get("couponKey") if isinstance(coupon, dict) else {}
        new_coupon_id = (
            str(coupon_key.get("couponId") or "")
            if isinstance(coupon_key, dict)
            else ""
        )
        return {
            "status": "created",
            "coupon_id": coupon_id,
            "new_coupon_id": new_coupon_id,
            "store_id": store.id,
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "days": int(days),
            "result": result,
        }

    def open_official_customer_console(self, store: Store) -> None:
        PlaywrightError, sync_playwright = playwright_api()

        storage_path = self.data_dir / "edge_profiles" / f"store_{store.id}"
        storage_path.mkdir(parents=True, exist_ok=True)
        payload = self.db.get_web_cookies(store.id) or {}
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(storage_path),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=["--start-maximized", "--no-first-run"],
                ),
            )
            for extra_page in list(context.pages[1:]):
                try:
                    extra_page.close()
                except Exception:
                    pass
            page = context.pages[0] if context.pages else context.new_page()
            # Persistent profiles can silently restore another shop. Clear
            # cookies and page storage before loading the selected store.
            context.clear_cookies()
            try:
                page.goto(
                    SHOP_HOME,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.evaluate(
                    "() => { localStorage.clear(); sessionStorage.clear(); }"
                )
            except PlaywrightError:
                pass
            context.clear_cookies()
            cookies_to_add = []
            for cookie in normalize_cookies(payload.get("cookies") or []):
                item = {
                    "name": cookie["name"],
                    "value": cookie["value"],
                    "domain": cookie.get("domain") or ".weixin.qq.com",
                    "path": cookie.get("path") or "/",
                }
                expires = cookie.get("expires")
                if isinstance(expires, (int, float)) and expires > 0:
                    item["expires"] = expires
                cookies_to_add.append(item)
            if cookies_to_add:
                context.add_cookies(cookies_to_add)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(WORKBENCH_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1800)
            try:
                candidate = page.locator("a, button, [role='menuitem']").filter(
                    has_text=re.compile(r"^\s*客服\s*$")
                ).first
                if candidate.count() and candidate.is_visible():
                    candidate.click(timeout=3000)
            except Exception:
                pass
            while context.pages:
                time.sleep(1)
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except PlaywrightError:
                    break
        finally:
            if payload.get("cookies"):
                status, detail = self.probe_payload(payload)
                self.db.set_web_session(store.id, payload, status, detail)
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()

    def open_official_backend(
        self,
        store: Store,
        url: str = WORKBENCH_URL,
        page_setup: Callable[[Any], None] | None = None,
    ) -> None:
        PlaywrightError, sync_playwright = playwright_api()
        storage_path = self.data_dir / "edge_profiles" / f"store_{store.id}"
        storage_path.mkdir(parents=True, exist_ok=True)
        payload = self.db.get_web_cookies(store.id) or {}
        playwright = sync_playwright().start()
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(
                str(storage_path),
                **browser_launch_options(
                    headless=False,
                    no_viewport=True,
                    args=["--start-maximized", "--no-first-run"],
                ),
            )
            for extra_page in list(context.pages[1:]):
                try:
                    extra_page.close()
                except Exception:
                    pass
            page = context.pages[0] if context.pages else context.new_page()
            context.clear_cookies()
            try:
                page.goto(
                    SHOP_HOME,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.evaluate(
                    "() => { localStorage.clear(); sessionStorage.clear(); }"
                )
            except PlaywrightError:
                pass
            context.clear_cookies()
            cookies_to_add = []
            for cookie in normalize_cookies(payload.get("cookies") or []):
                item = {
                    "name": cookie["name"],
                    "value": cookie["value"],
                    "domain": cookie.get("domain") or ".weixin.qq.com",
                    "path": cookie.get("path") or "/",
                }
                expires = cookie.get("expires")
                if isinstance(expires, (int, float)) and expires > 0:
                    item["expires"] = expires
                cookies_to_add.append(item)
            if cookies_to_add:
                context.add_cookies(cookies_to_add)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(800)
            if cookies_to_add:
                state = self._page_initial_state(page)
                actual_app_id = str(
                    (state.get("user_info") or {}).get("appid") or ""
                ).strip()
                if actual_app_id != store.app_id:
                    for _attempt in range(12):
                        if actual_app_id == store.app_id:
                            break
                        page.wait_for_timeout(250)
                        state = self._page_initial_state(page)
                        actual_app_id = str(
                            (state.get("user_info") or {}).get(
                                "appid"
                            )
                            or ""
                        ).strip()
                if actual_app_id and actual_app_id != store.app_id:
                    if self._switch_shop(page, store.app_id):
                        page.wait_for_timeout(1800)
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )
                        page.wait_for_timeout(1200)
                        state = self._page_initial_state(page)
                        actual_app_id = str(
                            (state.get("user_info") or {}).get(
                                "appid"
                            )
                            or ""
                        ).strip()
                if actual_app_id and actual_app_id != store.app_id:
                    raise WebSessionError(
                        f"后台店铺校验失败：目标 {store.name}"
                        f"（{store.app_id}），实际打开 {actual_app_id}"
                    )
            if page_setup is not None:
                page_setup(page)
                page.wait_for_timeout(800)
            while context.pages:
                time.sleep(1)
                try:
                    latest = normalize_cookies(context.cookies())
                    if latest:
                        payload["cookies"] = latest
                except PlaywrightError:
                    break
        finally:
            if payload.get("cookies"):
                status, detail = self.probe_payload(
                    payload,
                    store=store,
                    require_identity=True,
                )
                if status == "已登录":
                    self.db.set_web_session(
                        store.id,
                        payload,
                        status,
                        detail,
                    )
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()

    def open_official_order(self, store: Store, order_id: str) -> None:
        self.open_official_backend(
            store,
            f"https://store.weixin.qq.com/shop/order/detail?orderid={order_id}",
        )

    def open_official_product_backend(self, store: Store) -> None:
        self.open_official_backend(store, WORKBENCH_URL)

    def open_official_product(
        self,
        store: Store,
        product_id: str,
        *,
        target: str = "product",
    ) -> None:
        normalized_id = str(product_id or "").strip()
        if not normalized_id:
            raise ValueError("product_id 不能为空")
        # Product-level routes are not stable enough to hard-code. Always
        # enter from the product list, locate the exact ID, then click the
        # verified row action.
        path = "/shop/goods/list"

        def locate_product(page: Any) -> None:
            search = page.locator(
                "input[placeholder*='商品'], input[placeholder*='ID']"
            ).first
            if search.count() == 0:
                raise WebSessionError("商品列表页未找到商品搜索框")
            search.fill(normalized_id)
            search.press("Enter")
            page.wait_for_timeout(1800)
            matches = page.get_by_text(normalized_id, exact=True)
            count = matches.count()
            if count == 0:
                raise WebSessionError(
                    f"平台未找到商品 {normalized_id}，已停留在商品列表"
                )
            if count != 1:
                raise WebSessionError(
                    f"商品 {normalized_id} 搜索结果不唯一，已停止继续打开"
                )
            row = matches.first.locator("xpath=ancestor::tr[1]").first
            if row.count() == 0:
                row = matches.first.locator(
                    "xpath=ancestor::*[contains(@class,'table-row')][1]"
                ).first
            if row.count() == 0:
                raise WebSessionError("无法定位商品所在行，已停留在商品列表")
            label = "数据" if target in {"product_stats", "content", "growth"} else "编辑"
            action = row.get_by_text(label, exact=True).first
            if action.count() == 0:
                raise WebSessionError(
                    f"商品 {normalized_id} 所在行没有“{label}”入口"
                )
            action.click()
            page.wait_for_timeout(1600)
            if normalized_id not in page.locator("body").inner_text(timeout=5000):
                raise WebSessionError(
                    f"点击后未确认商品 {normalized_id}，已停止操作"
                )

        self.open_official_backend(
            store,
            f"https://store.weixin.qq.com{path}",
            page_setup=locate_product,
        )

    def probe(self, store: Store) -> tuple[str, str]:
        payload = self.db.get_web_cookies(store.id)
        if not payload:
            current = self.db.get_store(store.id)
            if current is not None and current.web_status in {
                "已掉线",
                "需重新扫码",
            }:
                detail = (
                    current.web_status_detail
                    or "微信后台登录已失效，请重新扫码"
                )
                self.db.update_web_status(
                    store.id,
                    current.web_status,
                    detail,
                )
                return current.web_status, detail
            self.db.update_web_status(store.id, "未登录", "请扫码登录或导入 Cookie")
            return "未登录", "请扫码登录或导入 Cookie"
        status, detail = self.probe_payload(payload, store=store)
        self.db.update_web_status(store.id, status, detail)
        return status, detail

    def probe_payload(
        self,
        payload: dict[str, Any],
        store: Store | None = None,
        require_identity: bool = False,
    ) -> tuple[str, str]:
        cookies = normalize_cookies(payload.get("cookies") or [])
        header = cookie_header(cookies)
        if not header:
            return "未登录", "没有有效 Cookie"
        session = requests.Session()
        session.trust_env = self.use_system_proxy
        try:
            response = session.get(
                WORKBENCH_URL,
                headers={
                    "Cookie": header,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/138.0 Safari/537.36",
                    "Referer": SHOP_HOME,
                },
                timeout=(5, 12),
                allow_redirects=True,
            )
        except requests.Timeout:
            return "网络异常", "登录状态检测超时"
        except requests.RequestException as exc:
            return "网络异常", str(exc)[:200]
        final_url = str(response.url or "")
        lowered = final_url.lower()
        if response.status_code in {401, 403}:
            return "已掉线", f"平台拒绝会话（HTTP {response.status_code}）"
        if any(marker in lowered for marker in ("/login", "/passport", "qrcode", "scan")):
            return "需重新扫码", "登录页已跳转，需要重新扫码"
        if response.status_code >= 500:
            return "平台异常", f"平台暂时不可用（HTTP {response.status_code}）"
        if response.status_code >= 400:
            return "连接异常", f"会话检测失败（HTTP {response.status_code}）"
        page_text = response.text[:500000]
        if (
            "登录超时" in page_text
            or "扫码进入我的小店" in page_text
            or "请重新登录" in page_text
        ):
            return "已掉线", "微信后台要求重新登录"
        if store is not None:
            identity = self.db.get_store_identity(store.id)
            if identity_tokens(identity):
                identities = self.db.list_store_identities()
                (
                    match_status,
                    _actual_store_id,
                    actual_name,
                ) = self._classify_identity(
                    page_text,
                    store,
                    identities,
                )
                if match_status == "mismatch":
                    return (
                        "已掉线",
                        f"当前网页登录的是“{actual_name}”，"
                        f"不是“{store.name}”，请重新扫码",
                    )
                if match_status == "ambiguous":
                    return (
                        "已掉线",
                        f"网页无法唯一确认店铺身份（{actual_name}）",
                    )
                if require_identity and match_status != "matched":
                    return (
                        "已掉线",
                        "无法确认网页所属店铺，登录信息未保存",
                    )
        return "已登录", "网页会话有效"

    def logout(self, store: Store) -> None:
        self.db.clear_web_session(store.id, "已手动退出网页会话")

    def cleanup_cache(self) -> int:
        """Remove disposable browser caches while preserving login data."""
        removed = 0
        cache_names = {
            "Cache",
            "Code Cache",
            "GPUCache",
            "DawnCache",
            "GraphiteDawnCache",
            "GrShaderCache",
            "ShaderCache",
        }
        for root_name in ("edge_profiles", "web_sessions"):
            root = (self.data_dir / root_name).resolve()
            if not root.is_dir():
                continue
            for profile in root.iterdir():
                try:
                    profile.resolve().relative_to(root)
                except (OSError, ValueError):
                    continue
                if not profile.is_dir():
                    continue
                for cache_name in cache_names:
                    target = profile / cache_name
                    if not target.is_dir():
                        continue
                    try:
                        target.resolve().relative_to(profile.resolve())
                        shutil.rmtree(target)
                    except (OSError, ValueError):
                        continue
                    removed += 1
        return removed

    @staticmethod
    def payload_summary(payload: dict[str, Any]) -> str:
        cookies = normalize_cookies(payload.get("cookies") or [])
        names = sorted({cookie["name"] for cookie in cookies})
        return json.dumps(
            {
                "cookies": len(cookies),
                "names": names[:12],
                "source_url": payload.get("source_url", ""),
            },
            ensure_ascii=False,
        )
