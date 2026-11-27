from __future__ import annotations

import gzip
import json
import mimetypes
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from .. import __version__
from ..auth_client import AuthError
from ..runtime_logging import log_event
from .controller import NextUiController


class FrontendServer:
    """Loopback-only HTTP adapter for the Web UI command interface."""

    def __init__(
        self,
        static_root: Path | Callable[[], Path],
        controller: NextUiController,
        token: str | None = None,
    ) -> None:
        self._static_root_provider = (
            static_root
            if callable(static_root)
            else lambda: Path(static_root)
        )
        self.controller = controller
        self.token = token or secrets.token_urlsafe(32)
        self._thread: threading.Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def static_root(self) -> Path:
        return Path(self._static_root_provider()).resolve()

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("FrontendServer has not been started")
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def action_url(self) -> str:
        return f"{self.base_url}/api/action"

    @property
    def entry_url(self) -> str:
        return f"{self.base_url}/?token={self.token}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        handler = self._handler_class()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        # Wait for active request workers during shutdown.  Leaving daemon
        # request threads behind can crash the interpreter while stderr is
        # being buffered during packaged Chromium test teardown.
        self._httpd.daemon_threads = False
        self._httpd.block_on_close = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="langlang-webui-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"LangLang/{__version__}"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                path = unquote(urlparse(self.path).path)
                if path in {"", "/"}:
                    path = "/index.html"
                static_root = owner.static_root
                target = (static_root / path.lstrip("/")).resolve()
                try:
                    target.relative_to(static_root)
                except ValueError:
                    self._send_json(
                        HTTPStatus.FORBIDDEN,
                        {"ok": False, "error": "非法文件路径"},
                    )
                    return
                if not target.is_file():
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"ok": False, "error": "文件不存在"},
                    )
                    return
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                content = target.read_bytes()
                content, encoding = self._compress(content)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                if encoding:
                    self.send_header("Content-Encoding", encoding)
                    self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                self.wfile.write(content)

            def do_POST(self) -> None:
                if urlparse(self.path).path != "/api/action":
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"ok": False, "error": "接口不存在"},
                    )
                    return
                if not secrets.compare_digest(
                    str(self.headers.get("X-LangLang-Session") or ""),
                    owner.token,
                ):
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED,
                        {"ok": False, "error": "本地会话无效"},
                    )
                    return
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    if length <= 0 or length > 32_000_000:
                        raise ValueError("请求体大小无效")
                    body = json.loads(self.rfile.read(length))
                    action = str(body.get("action") or "")
                    payload = body.get("payload")
                    if not isinstance(payload, dict):
                        payload = {}
                    origin = str(
                        self.headers.get("X-LangLang-Origin")
                        or "manual"
                    )[:80]
                    result = owner.controller.dispatch(
                        action,
                        payload,
                        origin=origin,
                    )
                except AuthError as exc:
                    log_event(
                        "webui.rpc_auth_error",
                        path=urlparse(self.path).path,
                        action=locals().get("action", ""),
                        error=str(exc),
                        code=str(getattr(exc, "code", "")),
                    )
                    status = (
                        exc.status
                        if exc.status in {
                            HTTPStatus.BAD_REQUEST,
                            HTTPStatus.UNAUTHORIZED,
                            HTTPStatus.FORBIDDEN,
                            HTTPStatus.SERVICE_UNAVAILABLE,
                        }
                        else HTTPStatus.FORBIDDEN
                    )
                    self._send_json(
                        status,
                        {
                            "ok": False,
                            "error": str(exc),
                            "code": exc.code,
                        },
                    )
                    return
                except ValueError as exc:
                    error_payload: dict[str, Any] = {
                        "ok": False,
                        "error": str(exc),
                    }
                    code = str(getattr(exc, "code", "") or "")
                    if code:
                        error_payload["code"] = code
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        error_payload,
                    )
                    return
                except Exception as exc:
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"ok": False, "error": str(exc)},
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "data": result},
                )

            def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
                content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                content, encoding = self._compress(content)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                if encoding:
                    self.send_header("Content-Encoding", encoding)
                    self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                self.wfile.write(content)

            def _compress(self, content: bytes) -> tuple[bytes, str]:
                accepts_gzip = "gzip" in str(
                    self.headers.get("Accept-Encoding") or ""
                ).lower()
                if not accepts_gzip or len(content) < 32_768:
                    return content, ""
                return gzip.compress(content, compresslevel=6), "gzip"

        return Handler
