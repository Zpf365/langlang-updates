from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weimi_manager.models import Store
from weimi_manager.web_session import WechatWebSession


class _FakePage:
    def goto(self, *_args, **_kwargs) -> None:
        return None

    def evaluate(self, *_args, **_kwargs) -> None:
        return None

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[_FakePage] = []

    def new_page(self) -> _FakePage:
        return _FakePage()

    def clear_cookies(self) -> None:
        return None

    def close(self) -> None:
        self.pages.clear()


class _FakeChromium:
    def __init__(self, context: _FakeContext) -> None:
        self.context = context
        self.launch_kwargs: dict[str, object] = {}

    def launch_persistent_context(self, *_args, **kwargs) -> _FakeContext:
        self.launch_kwargs = kwargs
        return self.context


class _FakePlaywright:
    def __init__(self) -> None:
        self.context = _FakeContext()
        self.chromium = _FakeChromium(self.context)

    def stop(self) -> None:
        return None


class _FakeDatabase:
    def get_web_cookies(self, _store_id: int) -> dict[str, object]:
        return {}


class VisualBrowserLayoutTests(unittest.TestCase):
    def test_visible_backend_browser_uses_native_maximized_viewport(self) -> None:
        fake_playwright = _FakePlaywright()
        manager = mock.Mock()
        manager.start.return_value = fake_playwright
        store = Store(
            id=1,
            name="测试店铺",
            app_id="wx-test",
            group_name="",
            status="正常",
            last_sync_at=None,
            created_at="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            session = WechatWebSession(_FakeDatabase(), Path(tmp))  # type: ignore[arg-type]
            with mock.patch(
                "weimi_manager.web_session.playwright_api",
                return_value=(RuntimeError, lambda: manager),
            ):
                session.open_official_backend(store)

        kwargs = fake_playwright.chromium.launch_kwargs
        self.assertTrue(kwargs.get("no_viewport"))
        self.assertNotIn("viewport", kwargs)
        self.assertIn("--start-maximized", kwargs.get("args", []))

    def test_open_backend_clears_stale_profile_and_verifies_store(self) -> None:
        class FakePage:
            url = "https://store.weixin.qq.com/shop/home"

            def __init__(self) -> None:
                self.goto_calls = 0

            def goto(self, *_args, **_kwargs) -> None:
                self.goto_calls += 1

            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

            def evaluate(self, script: str, *_args):
                if "__INITIAL_STATE__" in script:
                    return {
                        "user_info": {
                            "appid": "wx-target",
                            "nickName": "目标店铺",
                        },
                        "account_info": {},
                    }
                return None

        class FakeContext:
            def __init__(self) -> None:
                self.pages = []
                self.page = FakePage()
                self.clear_calls = 0
                self.added_cookies = []

            def clear_cookies(self) -> None:
                self.clear_calls += 1

            def new_page(self) -> FakePage:
                return self.page

            def add_cookies(self, cookies) -> None:
                self.added_cookies.extend(cookies)

            def close(self) -> None:
                return None

            def cookies(self):
                return self.added_cookies

        class FakeChromium:
            def __init__(self, context: FakeContext) -> None:
                self.context = context

            def launch_persistent_context(self, *_args, **_kwargs):
                return self.context

        class FakePlaywright:
            def __init__(self, context: FakeContext) -> None:
                self.chromium = FakeChromium(context)

            def stop(self) -> None:
                return None

        class FakeManager:
            def __init__(self, context: FakeContext) -> None:
                self.context = context

            def start(self) -> FakePlaywright:
                return FakePlaywright(self.context)

        class FakeDB:
            def __init__(self) -> None:
                self.saved = None

            def get_web_cookies(self, _store_id: int):
                return {
                    "cookies": [
                        {
                            "name": "sessionid",
                            "value": "target-cookie",
                            "domain": ".weixin.qq.com",
                            "path": "/",
                        }
                    ]
                }

            def set_web_session(
                self,
                store_id: int,
                payload: dict,
                status: str,
                detail: str,
            ) -> None:
                self.saved = (store_id, payload, status, detail)

        store = Store(
            id=7,
            name="目标店铺",
            app_id="wx-target",
            group_name="",
            status="正常",
            last_sync_at=None,
            created_at="",
        )
        context = FakeContext()
        db = FakeDB()
        with tempfile.TemporaryDirectory() as tmp:
            session = WechatWebSession(db, Path(tmp))  # type: ignore[arg-type]
            session.probe_payload = mock.Mock(
                return_value=("已登录", "有效")
            )
            with mock.patch(
                "weimi_manager.web_session.playwright_api",
                return_value=(
                    RuntimeError,
                    lambda: FakeManager(context),
                ),
            ):
                session.open_official_backend(store)

        self.assertGreaterEqual(context.clear_calls, 2)
        self.assertEqual(
            [item["value"] for item in context.added_cookies],
            ["target-cookie"],
        )
        self.assertIsNotNone(db.saved)
        self.assertEqual(db.saved[0], store.id)
        self.assertEqual(
            session.probe_payload.call_args.kwargs.get("store").id,
            store.id,
        )


if __name__ == "__main__":
    unittest.main()
