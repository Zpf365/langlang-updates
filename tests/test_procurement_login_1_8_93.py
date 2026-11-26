from __future__ import annotations

import inspect
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

from weimi_manager.db import Database
from weimi_manager.procurement import (
    ProcurementError,
    ProcurementWebSession,
    _wait_for_page_close,
    canonical_platform,
    procurement_login_identity,
    procurement_page_identity,
    procurement_profile_path,
)
from weimi_manager.service import SyncService
from weimi_manager.webui.controller import NextUiController


def test_chinese_taobao_platform_maps_to_official_login_key() -> None:
    assert canonical_platform("淘宝") == "taobao"
    assert canonical_platform("taobao") == "taobao"
    assert canonical_platform("1688") == "1688"
    with pytest.raises(ProcurementError, match="不支持"):
        canonical_platform("未知平台")


def test_taobao_identity_is_read_from_tracknick_cookie() -> None:
    assert procurement_login_identity(
        "淘宝",
        [
            {"name": "tracknick", "value": "fixture-buyer"},
            {"name": "cookie2", "value": "secret"},
        ],
    ) == "fixture-buyer"


def test_wait_for_page_close_uses_target_page_close_event() -> None:
    class Page:
        def __init__(self) -> None:
            self.events: list[tuple[str, int]] = []

        def is_closed(self) -> bool:
            return False

        def wait_for_event(self, event: str, *, timeout: int) -> None:
            self.events.append((event, timeout))

    page = Page()

    _wait_for_page_close(page)

    assert page.events == [("close", 0)]


def test_open_backend_does_not_wait_for_persistent_context_pages() -> None:
    source = inspect.getsource(ProcurementWebSession.open_backend)

    assert "_wait_for_page_close" in source
    assert "while context.pages" not in source


def test_1688_identity_is_read_from_official_login_id_cookie() -> None:
    assert procurement_login_identity(
        "1688",
        [
            {"name": "__cn_logon__", "value": "true"},
            {"name": "__cn_logon_id__", "value": "fixture-buyer"},
            {"name": "cookie2", "value": "secret"},
        ],
    ) == "fixture-buyer"


def test_procurement_profiles_are_isolated_per_account(
    tmp_path: Path,
) -> None:
    first = procurement_profile_path(
        tmp_path / "data",
        {"id": 11, "platform": "淘宝"},
    )
    second = procurement_profile_path(
        tmp_path / "data",
        {"id": 12, "platform": "1688"},
    )

    assert first != second
    assert first.name == "account_11"
    assert second.name == "account_12"
    assert first.parent == second.parent


def test_procurement_session_and_password_are_encrypted_at_rest(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "测试采购账号",
        "test-login-id",
    )
    password_marker = "test-password-must-not-be-plaintext"
    cookie_marker = "test-cookie-must-not-be-plaintext"
    session = {
        "platform": "淘宝",
        "identity": "test-login-id",
        "cookies": [
            {"name": "tracknick", "value": cookie_marker},
        ],
    }

    database.set_procurement_account_secrets(
        account_id,
        password=password_marker,
    )
    database.set_procurement_session(
        account_id,
        session,
        "已登录",
        "",
    )

    with database.connect() as connection:
        account_secret = connection.execute(
            """
            SELECT password_protected
            FROM procurement_account_secrets
            WHERE account_id=?
            """,
            (account_id,),
        ).fetchone()[0]
        session_secret = connection.execute(
            """
            SELECT payload_protected
            FROM procurement_sessions
            WHERE account_id=?
            """,
            (account_id,),
        ).fetchone()[0]
    assert password_marker.encode() not in account_secret
    assert cookie_marker.encode() not in session_secret
    assert database.get_procurement_account_password(account_id) == password_marker
    assert database.get_procurement_session(account_id) == session


def test_procurement_page_identity_rejects_other_or_bare_login_flags() -> None:
    class Page:
        def evaluate(self, _script, expected):
            return "测试采购账号" if expected == "测试采购账号" else ""

    assert procurement_page_identity(
        Page(),
        "测试采购账号",
    ) == "测试采购账号"
    assert procurement_page_identity(Page(), "另一个账号") == ""


class _FakePage:
    def __init__(self) -> None:
        self.frames: list[object] = []
        self.url = "https://i.taobao.com/my_taobao.htm"

    def bring_to_front(self) -> None:
        return None

    def goto(self, *_args, **_kwargs) -> None:
        return None

    def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None

    def is_closed(self) -> bool:
        return False


class _FakeContext:
    def __init__(self) -> None:
        self.page = _FakePage()
        self.page_reads = 0
        self.closed = False

    @property
    def pages(self):
        self.page_reads += 1
        return [self.page] if self.page_reads <= 3 else []

    def cookies(self):
        return [
            {"name": "_m_h5_tk", "value": "token_123", "domain": ".taobao.com", "path": "/"},
            {"name": "cookie2", "value": "secret", "domain": ".taobao.com", "path": "/"},
            {"name": "tracknick", "value": "fixture-buyer", "domain": ".taobao.com", "path": "/"},
        ]

    def add_cookies(self, _cookies) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _BrowserType:
    def __init__(self) -> None:
        self.context = _FakeContext()
        self.options = None

    def launch_persistent_context(self, _profile, **options):
        self.options = options
        return self.context


class _Playwright:
    def __init__(self) -> None:
        self.chromium = _BrowserType()

    def stop(self) -> None:
        return None


class _StopAwarePlaywright(_Playwright):
    def __init__(self) -> None:
        super().__init__()
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _SyncPlaywright:
    def __init__(self, playwright: _Playwright) -> None:
        self.playwright = playwright

    def start(self):
        return self.playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


def test_taobao_scan_login_creates_visible_browser_and_persists_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "fixture-buyer",
        "fixture-buyer",
    )
    account = database.get_procurement_account(account_id)
    playwright = _StopAwarePlaywright()
    module = ModuleType("playwright.sync_api")
    module.Error = RuntimeError
    module.sync_playwright = lambda: _SyncPlaywright(playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    launch_options: dict = {}
    monkeypatch.setattr(
        "weimi_manager.procurement.browser_launch_options",
        lambda **kwargs: launch_options.update(kwargs) or {},
    )

    session = ProcurementWebSession(database, tmp_path / "data")
    payload = session.login(account, auto_fill=False)

    assert launch_options["headless"] is False
    assert "--start-maximized" in launch_options["args"]
    assert payload["platform"] == "淘宝"
    assert payload["identity"] == "fixture-buyer"
    saved = database.get_procurement_session(account_id)
    assert saved is not None
    assert saved["identity"] == "fixture-buyer"
    assert database.get_procurement_account(account_id)["status"] == "已登录"
    assert playwright.chromium.context.closed is True
    assert playwright.stopped is True


def test_saved_procurement_session_is_revalidated_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "fixture-buyer",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account_id,
        {
            "platform": "淘宝",
            "identity": "fixture-buyer",
            "cookies": [
                {"name": "_m_h5_tk", "value": "token_123"},
                {"name": "cookie2", "value": "secret"},
                {"name": "tracknick", "value": "fixture-buyer"},
            ],
        },
        "已登录",
        "",
    )
    playwright = _StopAwarePlaywright()
    module = ModuleType("playwright.sync_api")
    module.Error = RuntimeError
    module.sync_playwright = lambda: _SyncPlaywright(playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setattr(
        "weimi_manager.procurement.browser_launch_options",
        lambda **_kwargs: {},
    )

    status = ProcurementWebSession(
        database,
        tmp_path / "data",
    ).check_status(database.get_procurement_account(account_id))

    assert status == "已登录"
    current = database.get_procurement_account(account_id)
    assert current["status"] == "已登录"
    assert current["nickname"] == "fixture-buyer"


def test_1688_session_without_identity_is_not_marked_logged_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "1688",
        "fixture-buyer",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account_id,
        {
            "platform": "1688",
            "identity": "",
            "cookies": [
                {
                    "name": "_m_h5_tk",
                    "value": "token_123",
                },
                {
                    "name": "__cn_logon__",
                    "value": "true",
                },
            ],
        },
        "已登录",
        "",
    )
    playwright = _StopAwarePlaywright()
    playwright.chromium.context.cookies = lambda: [
        {"name": "_m_h5_tk", "value": "token_123"},
        {"name": "__cn_logon__", "value": "true"},
    ]
    module = ModuleType("playwright.sync_api")
    module.Error = RuntimeError
    module.sync_playwright = lambda: _SyncPlaywright(playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setattr(
        "weimi_manager.procurement.browser_launch_options",
        lambda **_kwargs: {},
    )

    status = ProcurementWebSession(
        database,
        tmp_path / "data",
    ).check_status(database.get_procurement_account(account_id))

    assert status == "待验证"
    assert database.get_procurement_account(account_id)["status"] == "待验证"


class _StatusPage:
    def __init__(self, url: str) -> None:
        self.url = url

    def goto(self, *_args, **_kwargs) -> None:
        return None

    def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None


class _StatusContext:
    def __init__(self, final_url: str, cookies: list[dict]) -> None:
        self.page = _StatusPage(final_url)
        self._cookies = cookies
        self.closed = False

    @property
    def pages(self):
        return [self.page]

    def add_cookies(self, _cookies) -> None:
        return None

    def cookies(self):
        return list(self._cookies)

    def close(self) -> None:
        self.closed = True


class _StatusBrowserType:
    def __init__(self, context: _StatusContext) -> None:
        self.context = context

    def launch_persistent_context(self, _profile, **_options):
        return self.context


class _StatusPlaywright:
    def __init__(self, context: _StatusContext) -> None:
        self.chromium = _StatusBrowserType(context)


class _StatusSyncPlaywright:
    def __init__(self, playwright: _StatusPlaywright) -> None:
        self.playwright = playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


def test_inconclusive_1688_probe_keeps_saved_session_and_refreshes_cookies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "1688",
        "fixture-buyer",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account_id,
        {
            "identity": "fixture-buyer",
            "cookies": [
                {"name": "cookie2", "value": "old"},
            ],
        },
        "已登录",
        "",
    )
    context = _StatusContext(
        "https://work.1688.com/",
        [
            {"name": "csrf", "value": "temporary"},
        ],
    )
    module = ModuleType("playwright.sync_api")
    module.Error = RuntimeError
    module.sync_playwright = lambda: _StatusSyncPlaywright(
        _StatusPlaywright(context)
    )
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setattr(
        "weimi_manager.procurement.browser_launch_options",
        lambda **_kwargs: {},
    )

    status = ProcurementWebSession(
        database,
        tmp_path / "data",
    ).check_status(database.get_procurement_account(account_id))

    assert status == "已登录"
    current = database.get_procurement_account(account_id)
    assert current["status"] == "已登录"
    assert "本地加密会话已保留" in current["last_error"]
    session = database.get_procurement_session(account_id)
    assert session["identity"] == "fixture-buyer"
    assert session["last_status_check_at"]
    assert context.closed is True


def test_explicit_1688_login_redirect_marks_session_expired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "1688",
        "fixture-buyer",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account_id,
        {
            "identity": "fixture-buyer",
            "cookies": [
                {"name": "cookie2", "value": "old"},
            ],
        },
        "已登录",
        "",
    )
    context = _StatusContext(
        "https://login.1688.com/member/signin.htm",
        [
            {"name": "cookie2", "value": "old"},
            {"name": "_m_h5_tk", "value": "token"},
        ],
    )
    module = ModuleType("playwright.sync_api")
    module.Error = RuntimeError
    module.sync_playwright = lambda: _StatusSyncPlaywright(
        _StatusPlaywright(context)
    )
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setattr(
        "weimi_manager.procurement.browser_launch_options",
        lambda **_kwargs: {},
    )

    status = ProcurementWebSession(
        database,
        tmp_path / "data",
    ).check_status(database.get_procurement_account(account_id))

    assert status == "登录已失效"
    assert database.get_procurement_account(account_id)["status"] == "登录已失效"


def test_temporary_sync_failure_does_not_mark_saved_account_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "1688",
        "fixture-buyer",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account_id,
        {
            "identity": "fixture-buyer",
            "cookies": [{"name": "cookie2", "value": "saved"}],
        },
        "已登录",
        "",
    )

    class FailingAdapter:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def sync(self, *_args, **_kwargs):
            raise ProcurementError(
                "MTOP令牌已过期，页面安全验证稍后重试"
            )

    monkeypatch.setattr(
        "weimi_manager.service.ProcurementAdapter",
        FailingAdapter,
    )
    service = SyncService(database)

    with pytest.raises(ProcurementError, match="令牌已过期"):
        service.sync_procurement_account(account_id)

    current = database.get_procurement_account(account_id)
    assert current["status"] == "已登录"
    assert "令牌已过期" in current["last_error"]


def test_procurement_page_accepts_two_platforms_with_empty_passwords(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    controller = NextUiController(database)
    created = []
    for platform in ("淘宝", "1688"):
        created.append(
            controller.dispatch(
                "procurement.create",
                {
                    "platform": platform,
                    "label": "fixture-buyer",
                    "login_id": "fixture-buyer",
                    "password": "",
                },
            )["account"]
        )
    accounts = controller.dispatch("procurement.list", {})["accounts"]

    assert len(accounts) == 2
    assert [row["platform"] for row in accounts] == ["淘宝", "1688"]
    assert [row["label"] for row in accounts] == ["fixture-buyer", "fixture-buyer"]
    assert {
        int(row["id"])
        for row in accounts
    } == {
        int(row["id"])
        for row in created
    }


def test_taobao_identity_mismatch_does_not_save_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "data" / "weimi_manager.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "fixture-buyer",
        "fixture-buyer",
    )
    account = database.get_procurement_account(account_id)
    playwright = _StopAwarePlaywright()
    playwright.chromium.context.cookies = lambda: [
        {"name": "_m_h5_tk", "value": "token_123"},
        {"name": "cookie2", "value": "secret"},
        {"name": "tracknick", "value": "another-account"},
    ]
    module = ModuleType("playwright.sync_api")
    module.Error = RuntimeError
    module.sync_playwright = lambda: _SyncPlaywright(playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setattr(
        "weimi_manager.procurement.browser_launch_options",
        lambda **_kwargs: {},
    )

    session = ProcurementWebSession(database, tmp_path / "data")
    with pytest.raises(ProcurementError, match="不是目标账号"):
        session.login(account, auto_fill=False)

    current = database.get_procurement_account(account_id)
    assert current["status"] == "账号不匹配"
    assert "fixture-buyer" in current["last_error"]


def test_procurement_profile_cleanup_removes_only_target_account(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    first = database.add_procurement_account("淘宝", "first", "first")
    second = database.add_procurement_account("淘宝", "second", "second")
    root = tmp_path / "data" / "procurement_profiles"
    first_dir = root / f"account_{first}"
    second_dir = root / f"account_{second}"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    (first_dir / "cookie.bin").write_text("one", encoding="utf-8")
    (second_dir / "cookie.bin").write_text("two", encoding="utf-8")
    session = ProcurementWebSession(database, tmp_path / "data")

    errors = session.cleanup_profile(
        database.get_procurement_account(first)
    )

    assert errors == []
    assert first_dir.exists() is False
    assert second_dir.exists() is True


class _LoginService:
    def __init__(self, block: bool = False) -> None:
        self.block = block
        self.release = threading.Event()
        self.logins: list[int] = []

    def procurement_login(self, account_id: int, auto_fill: bool = True) -> dict:
        self.logins.append(account_id)
        if self.block:
            self.release.wait(timeout=3)
        return {"identity": "fixture-buyer"}

    def procurement_check_status(self, _account_id: int) -> str:
        return "已登录"

    def procurement_open_backend(self, account_id: int) -> dict:
        self.logins.append(account_id)
        if self.block:
            self.release.wait(timeout=3)
        return {
            "identity": "fixture-buyer",
            "opened_url": "https://i.taobao.com/my_taobao.htm",
            "final_url": "https://i.taobao.com/my_taobao.htm",
        }


def _wait_job(controller, job_id: str) -> dict:
    deadline = time.time() + 3
    while time.time() < deadline:
        job = controller.dispatch(
            "procurement.login.status",
            {"job_id": job_id},
        )
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("login job did not finish")


def test_webui_procurement_login_job_returns_before_browser_closes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "fixture-buyer",
        "fixture-buyer",
    )
    service = _LoginService()
    controller = NextUiController(database, product_service=service)

    started = controller.dispatch(
        "procurement.login.start",
        {"account_id": account_id, "scan_only": True},
    )
    job = _wait_job(controller, started["job_id"])

    assert job["status"] == "completed"
    assert job["identity"] == "fixture-buyer"
    assert service.logins == [account_id]


def test_webui_procurement_login_rejects_duplicate_window(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "fixture-buyer",
        "fixture-buyer",
    )
    service = _LoginService(block=True)
    controller = NextUiController(database, product_service=service)

    started = controller.dispatch(
        "procurement.login.start",
        {"account_id": account_id, "scan_only": True},
    )
    with pytest.raises(RuntimeError, match="已在运行"):
        controller.dispatch(
            "procurement.login.start",
            {"account_id": account_id, "scan_only": True},
        )
    service.release.set()
    job = _wait_job(controller, started["job_id"])
    assert job["status"] == "completed"


def test_webui_procurement_open_backend_uses_saved_session(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    account_id = database.add_procurement_account(
        "淘宝",
        "fixture-buyer",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account_id,
        {
            "identity": "fixture-buyer",
            "cookies": [{"name": "tracknick", "value": "fixture-buyer"}],
        },
        "已登录",
        "",
    )
    service = _LoginService()
    controller = NextUiController(database, product_service=service)

    started = controller.dispatch(
        "procurement.open-backend.start",
        {"account_id": account_id},
    )
    job = _wait_job(controller, started["job_id"])

    assert job["status"] == "completed"
    assert job["identity"] == "fixture-buyer"
    assert job["opened_url"].startswith("https://i.taobao.com/")
    assert service.logins == [account_id]


def test_webui_procurement_open_backend_requires_login(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    account_id = database.add_procurement_account(
        "1688",
        "fixture-buyer",
        "fixture-buyer",
    )
    controller = NextUiController(
        database,
        product_service=_LoginService(),
    )

    with pytest.raises(ValueError, match="先扫码登录"):
        controller.dispatch(
            "procurement.open-backend.start",
            {"account_id": account_id},
        )


def test_webui_procurement_open_backend_rejects_duplicate_window(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "webui.db")
    account_id = database.add_procurement_account(
        "1688",
        "fixture-buyer",
        "fixture-buyer",
    )
    database.set_procurement_session(
        account_id,
        {
            "identity": "fixture-buyer",
            "cookies": [{"name": "__cn_logon_id__", "value": "fixture-buyer"}],
        },
        "已登录",
        "",
    )
    service = _LoginService(block=True)
    controller = NextUiController(database, product_service=service)
    started = controller.dispatch(
        "procurement.open-backend.start",
        {"account_id": account_id},
    )

    with pytest.raises(RuntimeError, match="已在运行"):
        controller.dispatch(
            "procurement.open-backend.start",
            {"account_id": account_id},
        )
    service.release.set()
    assert _wait_job(
        controller,
        started["job_id"],
    )["status"] == "completed"
