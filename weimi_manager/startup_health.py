from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from .update_health import (
    mark_pending_update_healthy,
    read_update_attempt,
)

MarkerWriter = Callable[..., Path | None]


class StartupHealthCoordinator:
    """Own the one health marker write for this process startup."""

    def __init__(
        self,
        project_dir: Path,
        version: str,
        *,
        local_app_data: Path | None = None,
        attempt: Mapping[str, Any] | None = None,
        marker_writer: MarkerWriter | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.version = str(version)
        self.local_app_data = local_app_data
        self.attempt = dict(
            attempt
            if attempt is not None
            else read_update_attempt(
                self.project_dir,
                local_app_data=local_app_data,
            )
        )
        self.marker_writer = marker_writer or mark_pending_update_healthy
        self.startup_nonce = secrets.token_hex(16)
        self._lock = threading.Lock()
        self._marker_written = False
        self._auth_loop_entered = False
        self._auth_stable = False
        self._webui_expected = False
        self._server_started = False
        self._webui_loaded = False
        self._webui_ready = False
        attempt_path = str(self.attempt.get("install_path") or "").strip()
        self.active = bool(
            str(self.attempt.get("attempt_id") or "")
            and str(self.attempt.get("version") or "") == self.version
            and attempt_path
            and Path(attempt_path).resolve() == self.project_dir
        )

    def expect_webui(self) -> None:
        with self._lock:
            self._webui_expected = True

    def report_auth_loop_entered(self) -> None:
        with self._lock:
            self._auth_loop_entered = True
            self._publish_if_ready()

    def report_auth_stable(self) -> None:
        with self._lock:
            self._auth_stable = True
            self._publish_if_ready()

    def report_server_started(self) -> None:
        with self._lock:
            self._server_started = True
            self._publish_if_ready()

    def report_webui_loaded(self) -> None:
        with self._lock:
            self._webui_loaded = True
            self._publish_if_ready()

    def report_webui_ready(self) -> None:
        with self._lock:
            self._webui_ready = True
            self._publish_if_ready()

    def _publish_if_ready(self) -> None:
        if not self.active or self._marker_written:
            return
        if not (self._auth_loop_entered and self._auth_stable):
            return
        if self._webui_expected and not (
            self._server_started and self._webui_loaded and self._webui_ready
        ):
            return
        result = self.marker_writer(
            project_dir=self.project_dir,
            version=self.version,
            attempt_id=str(self.attempt["attempt_id"]),
            startup_nonce=self.startup_nonce,
            local_app_data=self.local_app_data,
        )
        if result is not None:
            self._marker_written = True
