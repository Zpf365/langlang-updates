from __future__ import annotations

import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Callable

from ..db import Database
from .adapter import AnalyticsEvent, registry
from .modules import register_builtin_adapters
from .reporter import AnalyticsConfig, AnalyticsReporter


ANALYTICS_ENABLED_SETTING = "ops_analytics_enabled"
ANALYTICS_BASE_URL_SETTING = "ops_analytics_base_url"
ANALYTICS_DEVICE_SETTING = "ops_analytics_device_ref"
ANALYTICS_PRIVACY_VERSION_SETTING = "ops_analytics_privacy_version"
ANALYTICS_PRIVACY_VERSION = "2026-11-25"


def load_analytics_config(
    db: Database,
    default_base_url: str = "",
) -> AnalyticsConfig:
    enabled_text = (
        os.environ.get("LANGLANG_ANALYTICS_ENABLED")
        or db.get_setting(ANALYTICS_ENABLED_SETTING, "1")
    ).strip().casefold()
    enabled = enabled_text not in {"0", "false", "off", "no"}
    base_url = (
        os.environ.get("LANGLANG_ANALYTICS_BASE_URL")
        or db.get_setting(ANALYTICS_BASE_URL_SETTING, "")
        or str(default_base_url or "")
    ).strip()
    return AnalyticsConfig(
        enabled=bool(enabled and base_url),
        base_url=base_url,
    )


class AnalyticsRuntime:
    def __init__(
        self,
        db: Database,
        *,
        token_provider: Callable[[], str],
        config: AnalyticsConfig | None = None,
        default_base_url: str = "",
    ) -> None:
        register_builtin_adapters()
        self.db = db
        self.token_provider = token_provider
        self.default_base_url = str(default_base_url or "")
        self.config = config or load_analytics_config(
            db,
            default_base_url=self.default_base_url,
        )
        self.device_ref = self._device_ref()
        self.reporter = AnalyticsReporter.get_or_create(
            db,
            self.config,
            token_provider,
        )
        if db.get_setting(ANALYTICS_PRIVACY_VERSION_SETTING, "") == "":
            db.set_setting(
                ANALYTICS_PRIVACY_VERSION_SETTING,
                ANALYTICS_PRIVACY_VERSION,
            )

    def _device_ref(self) -> str:
        existing = self.db.get_setting(ANALYTICS_DEVICE_SETTING, "").strip()
        if existing:
            return existing
        value = str(uuid.uuid4())
        self.db.set_setting(ANALYTICS_DEVICE_SETTING, value)
        return value

    def start(self) -> None:
        try:
            if self.config.enabled:
                self.reporter.start()
        except Exception:
            # Reporter startup is strictly best-effort.
            return

    def stop(self) -> None:
        try:
            self.reporter.stop()
        except Exception:
            return

    def emit(
        self,
        module_key: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        store_ref: str = "",
        batch_id: str = "",
        occurred_at: str | None = None,
        observed: bool = True,
    ) -> bool:
        started = time.perf_counter()
        try:
            adapter = registry.get(str(module_key))
            event = adapter.build_event(
                str(event_type),
                payload,
                device_ref=self.device_ref,
                store_ref=str(store_ref),
                batch_id=str(batch_id),
                occurred_at=occurred_at,
                observed=observed,
            )
            if event is None:
                return False
            # Keep the hot path as event projection + bounded in-memory enqueue.
            accepted = self.reporter.emit(event)
            _ = time.perf_counter() - started
            return accepted
        except Exception:
            return False

    def defer(self, task: Callable[[], None]) -> bool:
        try:
            return self.reporter.post(task)
        except Exception:
            return False

    def flush_now(self) -> dict[str, int]:
        try:
            return self.reporter.flush_now()
        except Exception:
            return {"sent": 0, "released": 0}

    def status(self) -> dict[str, Any]:
        try:
            return {
                "config": asdict(self.config),
                "device_ref": self.device_ref,
                "reporter": self.reporter.status(),
                "outbox": self.reporter.outbox.stats(),
                "modules": list(registry.module_keys()),
            }
        except Exception:
            return {
                "config": asdict(self.config),
                "device_ref": self.device_ref,
                "reporter": {},
                "outbox": {},
                "modules": list(registry.module_keys()),
            }

    def diagnostics(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            return self.reporter.outbox.diagnostics(limit)
        except Exception:
            return []

    def configure(
        self,
        *,
        enabled: bool,
        base_url: str,
    ) -> dict[str, Any]:
        self.db.set_setting(
            ANALYTICS_ENABLED_SETTING,
            "1" if enabled else "0",
        )
        self.db.set_setting(
            ANALYTICS_BASE_URL_SETTING,
            str(base_url or "").strip(),
        )
        self.config = load_analytics_config(
            self.db,
            default_base_url=self.default_base_url,
        )
        AnalyticsReporter.discard_for_database(self.db)
        self.reporter = AnalyticsReporter.get_or_create(
            self.db,
            self.config,
            self.token_provider,
        )
        self.start()
        return self.status()
