from __future__ import annotations

import faulthandler
import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import requests

_SENSITIVE_PATTERNS = (
    (
        re.compile(
            r"(?i)(cookie|authorization|token|secret|password)"
            r"\s*[:=]\s*\S+"
        ),
        r"\1=<REDACTED>",
    ),
    (re.compile(r"\b1[3-9]\d{9}\b"), "<PHONE>"),
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    (re.compile(r"\bwx[0-9a-fA-F]{16}\b"), "<WECHAT_APPID>"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<TOKEN>"),
)


def redact_text(value: str, limit: int = 8000) -> str:
    text = str(value or "")
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit]


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "<TRUNCATED>"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(
                r"(?i)(cookie|token|secret|password|authorization|address|phone)",
                key_text,
            ):
                result[key_text] = "<REDACTED>"
            else:
                result[key_text] = redact_value(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(repr(value))


class DiagnosticManager:
    """Offline-first diagnostics with redaction and HTTPS batch upload."""

    def __init__(
        self,
        data_dir: Path,
        version: str,
        *,
        account_id: str = "",
        username: str = "",
        device_hash: str = "",
        upload_url: str = "",
        access_token: str = "",
        access_token_provider: Callable[[], str] | None = None,
        access_token_refresher: Callable[[str], str] | None = None,
        tester_key: str = "",
        auto_upload: bool = True,
        crash_log_path: Path | None = None,
        stall_threshold: float = 5.0,
        stall_check_interval: float = 1.0,
    ) -> None:
        self.root = Path(data_dir) / "diagnostics"
        self.outbox = self.root / "outbox"
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "diagnostics.log"
        self.crash_path = self.root / "faulthandler.log"
        self.runtime_path = self.root / "runtime_state.json"
        self.crash_log_path = (
            Path(crash_log_path) if crash_log_path is not None else None
        )
        self.version = version
        self.account_id = account_id
        self.username = username
        self.device_hash = device_hash
        self.upload_url = upload_url
        self.access_token = access_token
        self.access_token_provider = access_token_provider
        self.access_token_refresher = access_token_refresher
        self.tester_key = tester_key
        self.auto_upload = auto_upload
        self.context_provider: Callable[[], dict[str, Any]] | None = None
        self._flush_lock = threading.Lock()
        self._runtime_lock = threading.Lock()
        self._session_id = uuid.uuid4().hex
        self._started_at = datetime.now(UTC)
        self._last_heartbeat_monotonic = time.monotonic()
        self._last_heartbeat_at = self._started_at
        self._last_runtime_write = 0.0
        self._active_route = ""
        self._stall_threshold = max(0.5, float(stall_threshold))
        self._stall_check_interval = max(0.1, float(stall_check_interval))
        self._stall_since: float | None = None
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._closed = False
        self._main_thread_id = threading.main_thread().ident
        self._original_hooks = (
            sys.excepthook,
            threading.excepthook,
        )
        self.start_session()

    @property
    def upload_ready(self) -> bool:
        """Return whether the remote receiver and a credential are configured."""
        return bool(
            self.upload_url
            and (self._current_access_token() or self.tester_key)
        )

    def _current_access_token(self) -> str:
        if self.access_token_provider is not None:
            try:
                current = str(self.access_token_provider() or "").strip()
            except Exception:
                current = ""
            if current:
                self.access_token = current
                return current
        return str(self.access_token or "").strip()

    def _refresh_access_token(self, failed_token: str) -> str:
        if self.access_token_refresher is None:
            return ""
        try:
            current = str(
                self.access_token_refresher(failed_token) or ""
            ).strip()
        except Exception:
            return ""
        if current:
            self.access_token = current
        return current

    @staticmethod
    def _process_metrics() -> dict[str, Any]:
        return {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pid": os.getpid(),
            "threads": threading.active_count(),
            "main_thread_id": threading.main_thread().ident,
        }

    def _read_runtime_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(
                self.runtime_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_runtime_state(self, *, clean_exit: bool) -> None:
        now = datetime.now(UTC)
        payload = {
            "session_id": self._session_id,
            "pid": os.getpid(),
            "version": self.version,
            "account_id": self.account_id,
            "username": self.username,
            "started_at": self._started_at.isoformat(timespec="seconds"),
            "last_heartbeat_at": self._last_heartbeat_at.isoformat(
                timespec="seconds"
            ),
            "route": self._active_route,
            "clean_exit": clean_exit,
            "written_at": now.isoformat(timespec="seconds"),
        }
        with self._runtime_lock:
            temporary = self.runtime_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.runtime_path)
            self._last_runtime_write = time.monotonic()

    def _faulthandler_tail(
        self,
        previous_started_at: str = "",
    ) -> str:
        path = self.crash_log_path
        if path is None or not path.is_file():
            return ""
        try:
            started = datetime.fromisoformat(
                previous_started_at.replace("Z", "+00:00")
            )
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if started and modified < started:
                return ""
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 24000))
                text = handle.read().decode("utf-8", errors="replace")
        except (OSError, TypeError, ValueError):
            return ""
        return redact_text(text, 20000)

    def start_session(self) -> None:
        previous = self._read_runtime_state()
        if previous and not bool(previous.get("clean_exit")):
            stack_tail = self._faulthandler_tail(
                str(previous.get("started_at") or "")
            )
            self.record(
                "previous_session_crash",
                "上次运行没有正常退出，检测到可能的闪退或进程终止",
                severity="fatal",
                detail={
                    "previous_session": previous,
                    "faulthandler_tail": stack_tail,
                },
            )
        self._write_runtime_state(clean_exit=False)
        if (
            os.environ.get("LANGLANG_DISABLE_UI_WATCHDOG") != "1"
            and self._monitor_thread is None
        ):
            self._monitor_thread = threading.Thread(
                target=self._monitor_ui,
                daemon=True,
                name="langlang-ui-watchdog",
            )
            self._monitor_thread.start()

    def heartbeat(self, route: str = "", *, force_write: bool = False) -> None:
        self._last_heartbeat_monotonic = time.monotonic()
        self._last_heartbeat_at = datetime.now(UTC)
        if route:
            self._active_route = route
        if force_write:
            self._write_runtime_state(clean_exit=False)

    def mark_clean_exit(self) -> None:
        try:
            self._write_runtime_state(clean_exit=True)
        except OSError:
            pass

    def _main_thread_stack(self) -> str:
        frame = sys._current_frames().get(self._main_thread_id)
        if frame is None:
            return ""
        return redact_text(
            "".join(traceback.format_stack(frame)),
            20000,
        )

    def check_ui_stall(self) -> None:
        now = time.monotonic()
        lag = now - self._last_heartbeat_monotonic
        if lag >= self._stall_threshold and self._stall_since is None:
            self._stall_since = self._last_heartbeat_monotonic
            self.record(
                "ui_stall",
                f"界面主线程无响应约 {lag:.1f} 秒",
                severity="error",
                detail={
                    "stall_seconds": round(lag, 3),
                    "route": self._active_route,
                    "main_thread_stack": self._main_thread_stack(),
                },
            )
            return
        if (
            self._stall_since is not None
            and lag < max(1.0, self._stall_threshold / 2)
        ):
            duration = now - self._stall_since
            self._stall_since = None
            self.record(
                "ui_stall_recovered",
                f"界面主线程恢复响应，卡顿约 {duration:.1f} 秒",
                severity="info",
                detail={
                    "stall_seconds": round(duration, 3),
                    "route": self._active_route,
                },
            )

    def _monitor_ui(self) -> None:
        while not self._monitor_stop.wait(self._stall_check_interval):
            try:
                if time.monotonic() - self._last_runtime_write >= 5.0:
                    self._write_runtime_state(clean_exit=False)
                self.check_ui_stall()
            except Exception:
                continue

    def _write_local(self, report: dict[str, Any]) -> Path:
        path = self.outbox / f"{report['report_id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        files: list[tuple[float, Path]] = []
        for item in self.outbox.glob("*.json"):
            try:
                files.append((item.stat().st_mtime, item))
            except OSError:
                continue
        files.sort(key=lambda item: item[0])
        for _modified, old in files[:-200]:
            old.unlink(missing_ok=True)
        return path

    def record(
        self,
        category: str,
        message: str,
        *,
        severity: str = "error",
        detail: dict[str, Any] | None = None,
    ) -> Path:
        try:
            context = self.context_provider() if self.context_provider else {}
        except Exception as exc:
            context = {
                "context_error": (
                    f"{type(exc).__name__}: {redact_text(str(exc), 500)}"
                )
            }
        redacted = redact_value(
            {
                "category": category,
                "severity": severity,
                "message": redact_text(message),
                "route": str(context.get("route") or ""),
                "page": str(context.get("page") or ""),
                "task": context.get("task") or {},
                "detail": detail or {},
            }
        )
        report = {
            "report_id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "version": self.version,
            "account_id": self.account_id,
            "username": self.username,
            "device_hash": self.device_hash,
            **redacted,
            "process": {
                **self._process_metrics(),
                "session_id": self._session_id,
                "started_at": self._started_at.isoformat(
                    timespec="seconds"
                ),
            },
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False) + "\n")
        path = self._write_local(report)
        if self.auto_upload and severity.casefold() != "info":
            self.flush_async()
        return path

    def capture_exception(
        self,
        exc: BaseException,
        *,
        category: str = "exception",
        detail: dict[str, Any] | None = None,
    ) -> Path:
        stack = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        payload = dict(detail or {})
        chain: list[str] = []
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(
                f"{type(current).__name__}: "
                f"{redact_text(str(current), 2000)}"
            )
            current = current.__cause__ or current.__context__
        payload.update(
            {
                "traceback": redact_text(stack, 16000),
                "exception_type": type(exc).__name__,
                "exception_message": redact_text(str(exc), 4000),
                "exception_chain": chain,
                "root_cause": chain[-1] if chain else "",
                "main_thread_stack": self._main_thread_stack(),
            }
        )
        return self.record(
            category,
            f"{type(exc).__name__}: {exc}",
            severity="fatal" if category == "crash" else "error",
            detail=payload,
        )

    def install_exception_hooks(self, tk_root: Any | None = None) -> None:
        previous_sys, previous_thread = self._original_hooks

        def sys_hook(exc_type, exc, tb):
            self.capture_exception(exc, category="uncaught")
            previous_sys(exc_type, exc, tb)

        def thread_hook(args):
            self.capture_exception(
                args.exc_value or RuntimeError("unknown thread error"),
                category="thread",
                detail={"thread_name": args.thread.name if args.thread else ""},
            )
            previous_thread(args)

        sys.excepthook = sys_hook
        threading.excepthook = thread_hook
        if tk_root is not None:
            def tk_hook(exc_type, exc, tb):
                self.capture_exception(
                    exc,
                    category="tk_callback",
                    detail={"tk_type": getattr(exc_type, "__name__", "")},
                )

            tk_root.report_callback_exception = tk_hook

    def pending_count(self) -> int:
        return len(list(self.outbox.glob("*.json")))

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        result = []
        for path in sorted(
            self.outbox.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:limit]:
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return result

    def flush(self, timeout: float = 8.0) -> int:
        uploaded = 0
        access_token = self._current_access_token()
        if not self.upload_url or not (access_token or self.tester_key):
            return 0
        refreshed = False
        for path in sorted(self.outbox.glob("*.json")):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                headers = {"Content-Type": "application/json"}
                if access_token:
                    headers["Authorization"] = (
                        f"Bearer {access_token}"
                    )
                if self.tester_key:
                    headers["X-LangLang-Tester-Key"] = self.tester_key
                response = requests.post(
                    self.upload_url,
                    json={"report": report},
                    headers=headers,
                    timeout=(4, timeout),
                )
                if (
                    response.status_code == 401
                    and not refreshed
                    and self.access_token_refresher is not None
                    and access_token
                ):
                    access_token = self._refresh_access_token(
                        access_token
                    )
                    if not access_token:
                        break
                    refreshed = True
                    retry_headers = dict(headers)
                    retry_headers["Authorization"] = (
                        f"Bearer {access_token}"
                    )
                    response = requests.post(
                        self.upload_url,
                        json={"report": report},
                        headers=retry_headers,
                        timeout=(4, timeout),
                    )
                if response.status_code == 401:
                    break
                response.raise_for_status()
            except (OSError, ValueError, requests.RequestException):
                break
            path.unlink(missing_ok=True)
            uploaded += 1
        return uploaded

    def flush_async(self) -> None:
        if not self.upload_ready:
            return
        if not self._flush_lock.acquire(blocking=False):
            return

        def worker() -> None:
            try:
                self.flush()
            finally:
                self._flush_lock.release()

        threading.Thread(
            target=worker,
            daemon=True,
            name="langlang-diagnostics-upload",
        ).start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._monitor_stop.set()
        self.mark_clean_exit()
        if (
            self._monitor_thread is not None
            and self._monitor_thread is not threading.current_thread()
        ):
            self._monitor_thread.join(timeout=0.3)
        sys.excepthook, threading.excepthook = self._original_hooks


_faulthandler_stream = None


def enable_faulthandler(data_dir: Path) -> None:
    """Install process-level fatal crash capture once at application start."""
    global _faulthandler_stream
    if _faulthandler_stream is not None:
        return
    if not isinstance(data_dir, (str, os.PathLike)):
        return
    path = Path(data_dir) / "diagnostics" / "faulthandler.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    _faulthandler_stream = path.open("a", encoding="utf-8")
    faulthandler.enable(_faulthandler_stream, all_threads=True)
