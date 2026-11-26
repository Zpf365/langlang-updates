from __future__ import annotations

import atexit
import json
import os
import platform
import re
import shutil
import sys
import threading
import traceback
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Callable

from . import __version__

SENSITIVE_KEY_MARKERS = (
    "password",
    "secret",
    "appsecret",
    "app_secret",
    "cookie",
    "authorization",
    "access_token",
    "refresh_token",
    "apikey",
    "api_key",
    "session",
    "address",
    "phone",
    "mobile",
    "email",
)
_EMAIL_RE = re.compile(
    r"\b[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+\b",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(password|secret|app_secret|appsecret|cookie|authorization|"
    r"access_token|refresh_token|api_?key|session|token)\b"
    r"(\s*[:=]\s*)([^,;\s&]+)"
)
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|session|password|secret|api_?key)=)[^&\s]+"
)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(value).casefold())


def _sensitive_key(value: object) -> bool:
    normalized = _normalized_key(value)
    return any(
        marker.replace("_", "") in normalized.replace("_", "")
        for marker in SENSITIVE_KEY_MARKERS
    )


def redact_text(value: object, *, limit: int = 2000) -> str:
    text = str(value or "")
    text = _EMAIL_RE.sub("[REDACTED]", text)
    text = _PHONE_RE.sub("[REDACTED]", text)
    text = _KEY_VALUE_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    text = _URL_SECRET_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED]",
        text,
    )
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def redact_data(
    value: Any,
    *,
    depth: int = 0,
    text_limit: int = 2000,
) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = redact_text(key, limit=120)
            if _sensitive_key(key):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_data(
                    item,
                    depth=depth + 1,
                    text_limit=text_limit,
                )
        return result
    if isinstance(value, (list, tuple, set)):
        return [
            redact_data(item, depth=depth + 1, text_limit=text_limit)
            for item in list(value)[:100]
        ]
    if isinstance(value, bytes):
        return f"[BINARY {len(value)} bytes]"
    if isinstance(value, str):
        return redact_text(value, limit=text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(value, limit=text_limit)


def _write_test(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write-test-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


class RuntimeLogManager:
    def __init__(
        self,
        project_dir: Path,
        *,
        local_app_data: Path | None = None,
        retention_days: int = 30,
        max_bytes: int = 20 * 1024 * 1024,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.local_app_data = (
            Path(local_app_data).resolve()
            if local_app_data is not None
            else Path(
                os.environ.get("LOCALAPPDATA")
                or Path.home() / "AppData" / "Local"
            ).resolve()
        )
        override = os.environ.get("LANGLANG_LOG_ROOT", "").strip()
        candidates = (
            [Path(override).expanduser().resolve()]
            if override
            else [
                self.project_dir / "logs",
                self.local_app_data / "LangLang" / "logs",
            ]
        )
        self.log_root = next(
            (candidate for candidate in candidates if _write_test(candidate)),
            candidates[-1],
        )
        self.retention_days = max(1, int(retention_days))
        self.max_bytes = max(1024, int(max_bytes))
        self._now = now
        self.run_id = uuid.uuid4().hex
        self.started_at = self._now().astimezone()
        self.day_dir = self.log_root / self.started_at.strftime("%Y-%m-%d")
        self.day_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._throttle: dict[str, tuple[float, int]] = {}
        self._closed = False
        self._ensure_log_files()
        self._session_path = self.day_dir / f"session-{self.run_id}.json"
        self._cleanup_old_logs()
        self._write_session(
            {
                "run_id": self.run_id,
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "exit_at": "",
                "software_version": __version__,
                "package_type": (
                    "PyInstaller"
                    if getattr(sys, "frozen", False)
                    else "source"
                ),
                "windows": platform.platform(),
                "python": platform.python_version(),
                "runtime": sys.executable,
                "account": "",
                "account_id": "",
                "data_dir": "",
                "log_dir": str(self.day_dir),
            }
        )
        self.app(
            "run.started",
            version=__version__,
            run_id=self.run_id,
            package_type=(
                "PyInstaller"
                if getattr(sys, "frozen", False)
                else "source"
            ),
            windows=platform.platform(),
            python=platform.python_version(),
            log_dir=str(self.day_dir),
        )

    def _path(self, name: str) -> Path:
        return self.day_dir / name

    def _ensure_log_files(self) -> None:
        for name in (
            "app.log",
            "webui-events.jsonl",
            "network.jsonl",
            "errors.log",
        ):
            try:
                with self._lock:
                    self._path(name).touch(exist_ok=True)
            except OSError:
                continue

    def _rotate(self, path: Path) -> None:
        try:
            if not path.is_file() or path.stat().st_size < self.max_bytes:
                return
            stamp = datetime.now().strftime("%H%M%S%f")
            shard = path.with_name(
                f"{path.stem}.{stamp}{path.suffix}"
            )
            path.replace(shard)
        except OSError:
            return

    def _append_json(self, filename: str, payload: dict[str, Any]) -> None:
        path = self._path(filename)
        try:
            with self._lock:
                self._rotate(path)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            default=str,
                        )
                        + "\n"
                    )
        except OSError:
            return

    def _append_line(self, filename: str, line: str) -> None:
        path = self._path(filename)
        try:
            with self._lock:
                self._rotate(path)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line.rstrip() + "\n")
        except OSError:
            return

    def app(self, event: str, **fields: Any) -> None:
        timestamp = self._now().astimezone().isoformat(timespec="seconds")
        payload = {
            "timestamp": timestamp,
            "run_id": self.run_id,
            "event": redact_text(event, limit=200),
            **redact_data(fields),
        }
        self._append_json("app.log", payload)

    def webui_event(self, event: dict[str, Any]) -> None:
        payload = {
            "timestamp": self._now().astimezone().isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            **redact_data(event, text_limit=500),
        }
        self._append_json("webui-events.jsonl", payload)

    def network(
        self,
        *,
        action: str,
        store_id: Any = "",
        parameters: Any = None,
        status: str,
        duration_ms: float,
        origin: str = "manual",
        error_code: str = "",
        error: str = "",
    ) -> None:
        error_text = redact_text(error, limit=1000)
        throttle_key = f"{action}|{error_code}|{error_text[:160]}"
        suppressed = 0
        if status != "success":
            now = self._now().timestamp()
            previous = self._throttle.get(throttle_key)
            if previous is not None and now - previous[0] < 10:
                self._throttle[throttle_key] = (
                    previous[0],
                    previous[1] + 1,
                )
                return
            suppressed = previous[1] if previous else 0
            self._throttle[throttle_key] = (now, 0)
        payload = {
            "timestamp": self._now().astimezone().isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "action": redact_text(action, limit=200),
            "store_id": (
                str(store_id)
                if str(store_id or "").strip()
                else ""
            ),
            "parameters": redact_data(parameters or {}),
            "status": status,
            "duration_ms": round(max(0.0, float(duration_ms)), 3),
            "origin": origin,
            "error_code": redact_text(error_code, limit=200),
            "error": error_text,
            "suppressed_since_last": suppressed,
        }
        self._append_json("network.jsonl", payload)

    def error(
        self,
        event: str,
        message: object,
        *,
        traceback_text: str = "",
    ) -> None:
        timestamp = self._now().astimezone().isoformat(timespec="seconds")
        payload = {
            "timestamp": timestamp,
            "run_id": self.run_id,
            "event": redact_text(event, limit=200),
            "message": redact_text(message, limit=4000),
            "traceback": redact_text(traceback_text, limit=20_000),
        }
        self._append_json("errors.log", payload)
        self.app(
            "error_logged",
            message=payload["message"],
            traceback=payload["traceback"],
        )

    def update_session(self, **fields: Any) -> None:
        if self._closed:
            return
        payload = getattr(self, "_session", {})
        payload.update(redact_data(fields))
        self._write_session(payload)

    def _write_session(self, payload: dict[str, Any]) -> None:
        self._session = dict(payload)
        try:
            with self._lock:
                self._session_path.write_text(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                    encoding="utf-8",
                )
        except OSError:
            return

    def _cleanup_old_logs(self) -> None:
        cutoff = self.started_at.date() - timedelta(
            days=self.retention_days
        )
        removed: list[str] = []
        try:
            children = list(self.log_root.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            try:
                day = datetime.strptime(child.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day >= cutoff:
                continue
            try:
                shutil.rmtree(child)
                removed.append(child.name)
            except OSError:
                continue
        if removed:
            self.app(
                "logs.cleanup",
                retention_days=self.retention_days,
                removed=removed,
            )

    def stats(self) -> dict[str, Any]:
        errors = 0
        try:
            errors = sum(
                1
                for line in self._path("errors.log")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            )
        except OSError:
            pass
        return {
            "log_path": str(self.day_dir),
            "log_root": str(self.log_root),
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "recent_errors": errors,
            "version": __version__,
        }

    def export(self, days: int) -> Path:
        days = 7 if int(days) == 7 else 1
        cutoff = self.started_at.date() - timedelta(days=days - 1)
        export_root = self.log_root.parent / "log-exports"
        export_root.mkdir(parents=True, exist_ok=True)
        target = export_root / (
            f"LangLang-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            f"-{self.run_id[:8]}.zip"
        )
        with zipfile.ZipFile(
            target,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for day_dir in sorted(self.log_root.iterdir()):
                if not day_dir.is_dir():
                    continue
                try:
                    day = datetime.strptime(
                        day_dir.name,
                        "%Y-%m-%d",
                    ).date()
                except ValueError:
                    continue
                if day < cutoff or day > self.started_at.date():
                    continue
                for path in sorted(day_dir.rglob("*")):
                    if path.is_file():
                        archive.write(
                            path,
                            ("logs/" + path.relative_to(self.log_root).as_posix()),
                        )
        self.app(
            "logs.exported",
            days=days,
            path=str(target),
            size=target.stat().st_size,
        )
        return target

    def close(self) -> None:
        if self._closed:
            return
        self.update_session(
            exit_at=self._now().astimezone().isoformat(timespec="seconds")
        )
        self._closed = True
        self.app("run.finished", run_id=self.run_id)


_manager: RuntimeLogManager | None = None
_manager_lock = threading.Lock()


def configure_runtime_logging(
    project_dir: Path,
    *,
    local_app_data: Path | None = None,
    force: bool = False,
    retention_days: int = 30,
    max_bytes: int = 20 * 1024 * 1024,
) -> RuntimeLogManager:
    global _manager
    with _manager_lock:
        if _manager is not None and not force:
            return _manager
        if _manager is not None:
            _manager.close()
        _manager = RuntimeLogManager(
            project_dir,
            local_app_data=local_app_data,
            retention_days=retention_days,
            max_bytes=max_bytes,
        )
        _install_exception_hooks(_manager)
        return _manager


def get_runtime_log_manager() -> RuntimeLogManager | None:
    return _manager


def log_event(event: str, **fields: Any) -> None:
    manager = _manager
    if manager is not None:
        manager.app(event, **fields)


def log_error(event: str, message: object) -> None:
    manager = _manager
    if manager is None:
        return
    trace = (
        "".join(
            traceback.format_exception(
                type(message),
                message,
                message.__traceback__,
            )
        )
        if isinstance(message, BaseException)
        else ""
    )
    manager.error(event, message, traceback_text=trace)


def _install_exception_hooks(manager: RuntimeLogManager) -> None:
    def excepthook(
        exception_type: type[BaseException],
        value: BaseException,
        tb: TracebackType | None,
    ) -> None:
        manager.error(
            "unhandled_exception",
            value,
            traceback_text="".join(
                traceback.format_exception(exception_type, value, tb)
            ),
        )

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        manager.error(
            "unhandled_thread_exception",
            args.exc_value,
            traceback_text="".join(
                traceback.format_exception(
                    args.exc_type,
                    args.exc_value,
                    args.exc_traceback,
                )
            ),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    atexit.register(manager.close)
