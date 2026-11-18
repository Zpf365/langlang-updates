from __future__ import annotations

import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from weimi_manager.config import (
    _database_store_count,
    _migrate_legacy_database,
    migrate_existing_user_data,
)
from weimi_manager.web_session import (
    MULTI_LOGIN_URL,
    extract_shop_switch_accounts,
    filter_shop_switch_accounts,
    normalize_shop_switch_account,
    select_preferred_shop_accounts,
    WechatWebSession,
)


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
                "INSERT INTO stores (name, app_id, secret_protected) "
                "VALUES (?, ?, ?)",
                (name, f"wx-{index}", b"secret"),
            )
        conn.commit()
    finally:
        conn.close()


class UpgradePersistenceTests(unittest.TestCase):
    def test_empty_target_is_replaced_by_legacy_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "old" / "data"
            target = root / "LangLang" / "data"
            _create_store_database(legacy / "weimi_manager.db", ["A", "B"])
            _create_store_database(target / "weimi_manager.db", [])
            self.assertTrue(_migrate_legacy_database(legacy, target))
            self.assertEqual(
                _database_store_count(target / "weimi_manager.db"),
                2,
            )
            self.assertTrue(
                list((target / "backups").glob("pre-migration-*.db"))
            )

    def test_nonempty_target_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "old" / "data"
            target = root / "LangLang" / "data"
            _create_store_database(legacy / "weimi_manager.db", ["A", "B"])
            _create_store_database(target / "weimi_manager.db", ["Current"])
            self.assertFalse(_migrate_legacy_database(legacy, target))
            conn = sqlite3.connect(target / "weimi_manager.db")
            try:
                names = [
                    row[0] for row in conn.execute("SELECT name FROM stores")
                ]
            finally:
                conn.close()
            self.assertEqual(names, ["Current"])

    def test_renamed_sibling_distribution_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "LangLang-1.6.21"
            project.mkdir()
            legacy = root / "LangLang-1.6.20" / "data"
            _create_store_database(legacy / "weimi_manager.db", ["A", "B"])
            target = project / "profile" / "data"
            with mock.patch.dict(
                "os.environ",
                {
                    "LOCALAPPDATA": str(root / "local"),
                    "APPDATA": str(root / "roaming"),
                },
            ):
                source = migrate_existing_user_data(project, target)
            self.assertEqual(source, legacy)
            self.assertEqual(
                _database_store_count(target / "weimi_manager.db"),
                2,
            )


class MultiShopLoginTests(unittest.TestCase):
    def test_multi_login_url_uses_official_pc_multi_account_entry(self) -> None:
        self.assertEqual(
            MULTI_LOGIN_URL,
            "https://store.weixin.qq.com/shop"
            "?action=pcLogin&multiLogin=1",
        )

    def test_accepts_clickable_text_login_control(self) -> None:
        class Locator:
            def __init__(self, visible: bool = True) -> None:
                self.visible = visible
                self.clicked = False

            @property
            def first(self):
                return self

            @property
            def last(self):
                return self

            def count(self) -> int:
                return 1 if self.visible else 0

            def is_visible(self) -> bool:
                return self.visible

            def is_enabled(self) -> bool:
                return self.visible

            def click(self) -> None:
                self.clicked = True

        class Body:
            def inner_text(self, timeout: int) -> str:
                return "选择要登录的账号\n全选\n登录"

        class Page:
            def __init__(self) -> None:
                self.select_all = Locator()
                self.login = Locator()

            def locator(self, selector: str):
                return Body()

            def get_by_text(self, text: str, exact: bool = False):
                return self.select_all if text == "全选" else self.login

            def get_by_role(self, role: str, name: str, exact: bool = False):
                return Locator(visible=False)

            def wait_for_timeout(self, milliseconds: int) -> None:
                return

        page = Page()
        self.assertTrue(WechatWebSession._accept_multi_account_login(page))
        self.assertTrue(page.select_all.clicked)
        self.assertTrue(page.login.clicked)

    def test_does_not_submit_login_before_select_all_succeeds(self) -> None:
        class Locator:
            def __init__(self, visible: bool) -> None:
                self.visible = visible
                self.clicked = False

            @property
            def first(self):
                return self

            @property
            def last(self):
                return self

            def count(self) -> int:
                return 1

            def is_visible(self) -> bool:
                return self.visible

            def is_enabled(self) -> bool:
                return True

            def click(self) -> None:
                self.clicked = True

        class Body:
            def inner_text(self, timeout: int) -> str:
                return "选择要登录的账号\n全选\n登录"

        class Page:
            def __init__(self) -> None:
                self.select_all = Locator(False)
                self.login = Locator(True)

            def locator(self, selector: str):
                return Body()

            def get_by_role(self, role: str, name: str, exact: bool = False):
                if name == "全选":
                    return self.select_all
                return self.login

            def get_by_text(self, text: str, exact: bool = False):
                if text == "全选":
                    return self.select_all
                return self.login

            def wait_for_timeout(self, milliseconds: int) -> None:
                return

        page = Page()
        self.assertFalse(WechatWebSession._accept_multi_account_login(page))
        self.assertFalse(page.select_all.clicked)
        self.assertFalse(page.login.clicked)

    def test_accepts_selection_page_inside_iframe(self) -> None:
        class Locator:
            @property
            def first(self):
                return self

            @property
            def last(self):
                return self

            def count(self) -> int:
                return 1

            def is_visible(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            def click(self) -> None:
                return

        class EmptyBody:
            def inner_text(self, timeout: int) -> str:
                return "微信小店登录"

        class SelectionBody:
            def inner_text(self, timeout: int) -> str:
                return "选择要登录的账号\n全选\n登录"

        class Frame:
            def locator(self, selector: str):
                return SelectionBody()

            def get_by_text(self, text: str, exact: bool = False):
                return Locator()

            def get_by_role(self, role: str, name: str, exact: bool = False):
                return Locator()

            def wait_for_timeout(self, milliseconds: int) -> None:
                return

        class Page:
            frames = [Frame()]

            def locator(self, selector: str):
                return EmptyBody()

        self.assertTrue(WechatWebSession._accept_multi_account_login(Page()))

    def test_multi_shop_capture_retries_until_list_is_complete(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.waits = 0

            def wait_for_timeout(self, milliseconds: int) -> None:
                self.waits += 1

            def is_closed(self) -> bool:
                return False

        results = iter(
            [
                {},
                {
                    "matched_store_ids": [1],
                    "discovered_count": 2,
                },
                {
                    "matched_store_ids": [1, 2],
                    "discovered_count": 2,
                },
            ]
        )
        session = WechatWebSession.__new__(WechatWebSession)
        session._log = lambda *_args, **_kwargs: None
        calls = 0

        def capture(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return next(results)

        session._capture_all_shop_logins = capture
        page = FakePage()
        result = session._capture_all_shop_logins_with_retries(
            page,
            object(),
        )
        self.assertEqual(result["matched_store_ids"], [1, 2])
        self.assertEqual(calls, 3)
        self.assertEqual(page.waits, 2)

    def test_single_store_login_also_captures_switch_list(self) -> None:
        class FakeError(Exception):
            pass

        class BodyLocator:
            def inner_text(self, timeout: int) -> str:
                return "微信小店首页"

        class FakePage:
            url = "https://store.weixin.qq.com/shop/home"

            def locator(self, _selector: str) -> BodyLocator:
                return BodyLocator()

            def evaluate(self, *_args, **_kwargs) -> None:
                return None

            def goto(self, *_args, **_kwargs) -> None:
                return None

            def is_closed(self) -> bool:
                return False

            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

            @property
            def frames(self):
                return [self]

        class FakeContext:
            def __init__(self) -> None:
                self.pages = [FakePage()]

            def on(self, *_args, **_kwargs) -> None:
                return None

            def clear_cookies(self) -> None:
                return None

            def cookies(self):
                return [
                    {"name": "sessionid", "value": "cookie-a"},
                    {"name": "bizuin", "value": "cookie-b"},
                ]

            def close(self) -> None:
                return None

        class FakeBrowser:
            def launch_persistent_context(self, *_args, **_kwargs):
                return FakeContext()

        class FakePlaywright:
            chromium = FakeBrowser()

            def stop(self) -> None:
                return None

        class FakeManager:
            def start(self) -> FakePlaywright:
                return FakePlaywright()

        class FakeDB:
            def __init__(self) -> None:
                self.session = None

            def list_store_identities(self):
                return []

            def list_stores(self):
                return [store]

            def get_store(self, store_id: int):
                return store if store_id == store.id else None

            def set_web_session(
                self,
                store_id: int,
                payload: dict,
                status: str,
                detail: str,
            ) -> None:
                self.session = (store_id, payload, status, detail)

        store = SimpleNamespace(
            id=1,
            name="测试店铺",
            app_id="wx-store-one",
        )
        db = FakeDB()
        session = WechatWebSession(db, Path(tempfile.gettempdir()))
        session._store_identity = lambda _store, refresh=False: {
            "store_name": store.name,
        }
        session._page_initial_state = lambda _page: {
            "user_info": {"appid": store.app_id},
            "account_info": {},
        }
        session._identify_logged_in_store = lambda *_args, **_kwargs: (
            store.id,
            store.name,
        )
        session.probe_payload = lambda *_args, **_kwargs: (
            "已登录",
            "网页会话有效",
        )
        enumeration = mock.Mock(return_value={})
        session._capture_all_shop_logins_with_retries = enumeration

        with mock.patch(
            "weimi_manager.web_session.playwright_api",
            return_value=(FakeError, lambda: FakeManager()),
        ):
            payload = session.interactive_login(
                store,
                allowed_store_ids={store.id},
            )

        enumeration.assert_called_once()
        self.assertTrue(payload["login_confirmed"])
        self.assertEqual(payload["matched_store_id"], store.id)
        self.assertIsNotNone(db.session)

    def test_extracts_wrapped_switch_list(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {"appid": "wx-a", "nickname": "A"},
                    {"appid": "wx-b", "nickname": "B"},
                ]
            },
        }
        self.assertEqual(len(extract_shop_switch_accounts(payload)), 2)

    def test_filters_switch_list_to_local_store_app_ids(self) -> None:
        accounts = [
            {"appid": "wx-a", "nickname": "A"},
            {"appid": "wx-b", "nickname": "B"},
            {"appid": "WX-C", "nickname": "C"},
        ]
        selected = filter_shop_switch_accounts(
            accounts,
            {"wx-a", "wx-c"},
        )
        self.assertEqual(
            [item["appid"] for item in selected],
            ["wx-a", "WX-C"],
        )

    def test_shop_switch_list_uses_official_query_parameters(self) -> None:
        session = WechatWebSession.__new__(WechatWebSession)
        captured: dict[str, str] = {}

        def request(_page, method: str, url: str, body=None):
            captured["method"] = method
            captured["url"] = url
            return {
                "status": 200,
                "payload": {
                    "code": 0,
                    "list": [
                        {"appid": "wx-a", "nickname": "A"},
                        {"appid": "wx-b", "nickname": "B"},
                    ],
                },
            }

        session._request_json = request
        accounts = session._fetch_shop_switch_accounts(object())
        self.assertEqual(len(accounts), 2)
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(
            captured["url"],
            "/shop-faas/mmecnodelogin/session/getShopSwitchList"
            "?token=&lang=zh_CN",
        )

    def test_shop_switch_request_includes_captured_magic_headers(self) -> None:
        session = WechatWebSession.__new__(WechatWebSession)
        session._request_headers = {
            "biz_magic": "official-value",
            "potter-scene": "",
        }
        captured = {}

        class Page:
            def evaluate(self, _script, payload):
                captured.update(payload)
                return {
                    "status": 200,
                    "payload": {"code": 0, "list": []},
                }

        session._request_json(Page(), "GET", "/example")
        self.assertEqual(
            captured["extraHeaders"]["biz_magic"],
            "official-value",
        )

    def test_official_magic_headers_are_captured_and_replaced(self) -> None:
        class Request:
            def all_headers(self):
                return {
                    "biz_magic": "current-value",
                    "logistics_magic": "",
                    "potter-scene": "",
                }

        session = WechatWebSession.__new__(WechatWebSession)
        session._request_headers = {"biz_magic": "old-value"}
        session._capture_official_request_headers(Request())
        self.assertEqual(
            session._request_headers["biz_magic"],
            "current-value",
        )

    def test_prefers_all_subaccounts_and_skips_main(self) -> None:
        accounts = [
            {
                "app_id": "wx-main",
                "name": "主账号",
                "account_role": "main",
            },
            {
                "app_id": "wx-sub-1",
                "name": "子账号 1",
                "account_role": "sub",
            },
            {
                "app_id": "wx-sub-2",
                "name": "子账号 2",
                "account_role": "sub",
            },
        ]
        selected, excluded = select_preferred_shop_accounts(accounts)
        self.assertEqual(
            [item["app_id"] for item in selected],
            ["wx-sub-1", "wx-sub-2"],
        )
        self.assertEqual(
            [item["app_id"] for item in excluded],
            ["wx-main"],
        )

    def test_keeps_every_subaccount_when_many_are_visible(self) -> None:
        accounts = [
            {
                "app_id": f"wx-sub-{index}",
                "name": f"子账号 {index}",
                "account_role": "sub",
            }
            for index in range(15)
        ]
        selected, excluded = select_preferred_shop_accounts(accounts)
        self.assertEqual(len(selected), 15)
        self.assertFalse(excluded)

    def test_main_only_is_kept(self) -> None:
        accounts = [
            {
                "app_id": "wx-main",
                "name": "主账号",
                "account_role": "main",
            }
        ]
        selected, excluded = select_preferred_shop_accounts(accounts)
        self.assertEqual(len(selected), 1)
        self.assertFalse(excluded)

    def test_normalizes_initial_state_role(self) -> None:
        account = normalize_shop_switch_account(
            {"appid": "wx-a", "nickname": "店铺 A"},
            {
                "user_info": {
                    "appid": "wx-a",
                    "nickName": "店铺 A",
                    "isAdmin": False,
                },
                "account_info": {"nickName": "运营微信"},
            },
        )
        self.assertIsNotNone(account)
        self.assertEqual(account["account_role"], "sub")
        self.assertEqual(account["account_nickname"], "运营微信")

    def test_capture_saves_many_subaccounts_and_skips_main(self) -> None:
        class FakeDB:
            def __init__(self) -> None:
                self.sessions: dict[int, dict] = {}
                self.cleared: set[int] = set()
                self.next_id = 1
                self.ids: dict[str, int] = {}
                self.profiles: dict[int, dict] = {}

            def ensure_web_login_store(self, name: str, app_id: str):
                if app_id not in self.ids:
                    self.ids[app_id] = self.next_id
                    self.next_id += 1
                    return self.ids[app_id], True
                return self.ids[app_id], False

            def upsert_store_profile(self, store_id: int, info: dict) -> None:
                self.profiles[store_id] = dict(info)

            def set_web_session(
                self,
                store_id: int,
                payload: dict,
                status: str,
                detail: str,
            ) -> None:
                self.sessions[store_id] = {
                    "payload": dict(payload),
                    "status": status,
                }

            def clear_web_session(self, store_id: int, detail: str) -> None:
                self.sessions.pop(store_id, None)
                self.cleared.add(store_id)

        class FakePage:
            def __init__(self) -> None:
                self.app_id = "wx-main"
                self.url = "https://store.weixin.qq.com/shop/home"
                self.goto_calls = 0
                self.wait_calls = 0

            def wait_for_timeout(self, _milliseconds: int) -> None:
                self.wait_calls += 1

            def goto(self, *_args, **_kwargs) -> None:
                self.goto_calls += 1

        class FakeContext:
            def __init__(self, page: FakePage) -> None:
                self.page = page

            def cookies(self):
                return [
                    {
                        "name": "sessionid",
                        "value": f"cookie-{self.page.app_id}",
                        "domain": ".weixin.qq.com",
                        "path": "/",
                    }
                ]

        db = FakeDB()
        session = WechatWebSession(db, Path(tempfile.gettempdir()))
        page = FakePage()
        context = FakeContext(page)
        accounts = [
            {
                "appid": "wx-main",
                "nickname": "主账号",
                "isAdmin": True,
            },
            *[
                {
                    "appid": f"wx-sub-{index}",
                    "nickname": f"子账号 {index}",
                    "isAdmin": False,
                }
                for index in range(15)
            ],
        ]
        session._fetch_shop_switch_accounts = lambda _page: accounts

        def switch(_page, app_id: str) -> bool:
            page.app_id = app_id
            return True

        session._switch_shop = switch
        session._page_initial_state = lambda _page: {
            "user_info": {
                "appid": page.app_id,
                "nickName": page.app_id,
                "isAdmin": page.app_id == "wx-main",
            },
            "account_info": {"nickName": "同一个微信"},
        }
        session.probe_payload = lambda *_args, **_kwargs: (
            "已登录",
            "网页会话有效",
        )
        saved_snapshots: list[tuple[int, int]] = []

        def on_store_saved(store_id: int, _name: str) -> None:
            saved_snapshots.append((store_id, len(db.sessions)))

        result = session._capture_all_shop_logins(
            page,
            context,
            on_store_saved=on_store_saved,
        )
        self.assertEqual(len(result["matched_store_ids"]), 15)
        self.assertEqual(result["excluded_main_ids"], [db.ids["wx-main"]])
        self.assertEqual(len(db.sessions), 15)
        self.assertEqual(
            sorted(count for _store_id, count in saved_snapshots[:16]),
            list(range(1, 17)),
        )
        self.assertEqual(page.goto_calls, 15)
        self.assertEqual(page.wait_calls, 30)
        self.assertNotIn(db.ids["wx-main"], db.sessions)
        self.assertIn(db.ids["wx-main"], db.cleared)
        self.assertTrue(
            all(
                profile["account_role"] == "sub"
                for store_id, profile in db.profiles.items()
                if store_id != db.ids["wx-main"]
            )
        )

if __name__ == "__main__":
    unittest.main()
