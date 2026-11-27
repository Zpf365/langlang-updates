from __future__ import annotations

import json
import gzip
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from weimi_manager.db import Database
from weimi_manager import __version__
from weimi_manager.webui.controller import NextUiController
from weimi_manager.webui.server import FrontendServer


def test_frontend_server_rejects_missing_session(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    server = FrontendServer(
        static,
        NextUiController(Database(tmp_path / "webui.db")),
        "test-token",
    )
    server.start()
    try:
        request = Request(
            server.action_url,
            data=json.dumps({"action": "bootstrap", "payload": {}}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=3)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("missing session token must be rejected")
    finally:
        server.stop()


def test_frontend_server_serves_actions_and_static_files(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    server = FrontendServer(
        static,
        NextUiController(Database(tmp_path / "webui.db")),
        "test-token",
    )
    server.start()
    try:
        with urlopen(f"{server.base_url}/", timeout=3) as response:
            assert response.read() == b"<html>ok</html>"

        request = Request(
            server.action_url,
            data=json.dumps({"action": "bootstrap", "payload": {}}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-LangLang-Session": "test-token",
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
        assert payload["ok"] is True
        assert payload["data"]["brand"]["name"] == "澜琅"
        assert payload["data"]["brand"]["version"] == ".".join(
            __version__.split(".")[:3]
        )
    finally:
        server.stop()


def test_large_static_response_uses_gzip(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    content = b"x" * 40_000
    (static / "large.js").write_bytes(content)
    server = FrontendServer(
        static,
        NextUiController(Database(tmp_path / "webui.db")),
        "test-token",
    )
    server.start()
    try:
        request = Request(
            f"{server.base_url}/large.js",
            headers={"Accept-Encoding": "gzip"},
        )
        with urlopen(request, timeout=3) as response:
            assert response.headers.get("Content-Encoding") == "gzip"
            assert gzip.decompress(response.read()) == content
    finally:
        server.stop()


def test_frontend_server_uses_dynamic_static_root(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "index.html").write_text("<html>first</html>", encoding="utf-8")
    (second / "index.html").write_text("<html>second</html>", encoding="utf-8")
    active = {"root": first}
    server = FrontendServer(
        lambda: active["root"],
        NextUiController(Database(tmp_path / "webui.db")),
        "test-token",
    )
    server.start()
    try:
        with urlopen(f"{server.base_url}/", timeout=3) as response:
            assert response.read() == b"<html>first</html>"

        active["root"] = second

        with urlopen(f"{server.base_url}/", timeout=3) as response:
            assert response.read() == b"<html>second</html>"
    finally:
        server.stop()


def test_operations_permission_error_is_http_403_without_summary(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>", encoding="utf-8")
    database = Database(tmp_path / "webui.db")
    database.add_store("不可泄露的店铺", "wx-private-store", "secret")

    class DeniedClient:
        def organization_context(self, _access_token):
            return {
                "organization_id": "org-test",
                "role": "operator",
                "permissions": {},
            }

    controller = NextUiController(
        database,
        auth_context=SimpleNamespace(
            member_role="operator",
            permissions={},
            tokens=SimpleNamespace(access_token="test-token"),
        ),
        auth_client=DeniedClient(),
    )
    server = FrontendServer(static, controller, "test-token")
    server.start()
    try:
        request = Request(
            server.action_url,
            data=json.dumps(
                {"action": "operations.dashboard", "payload": {}}
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "X-LangLang-Session": "test-token",
            },
            method="POST",
        )
        try:
            urlopen(request, timeout=3)
        except HTTPError as exc:
            assert exc.code == 403
            body = json.loads(exc.read())
            assert body["ok"] is False
            assert "不可泄露的店铺" not in json.dumps(body, ensure_ascii=False)
            assert "stores" not in body
        else:
            raise AssertionError("operations must fail closed without permission")
    finally:
        server.stop()
