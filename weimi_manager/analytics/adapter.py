from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .privacy import payload_size, sanitize_payload


REPORT_ENDPOINTS = {
    "heartbeat": "/v1/report/heartbeat",
    "store-daily": "/v1/report/store-daily",
    "product-daily": "/v1/report/product-daily",
    "order-events": "/v1/report/order-events",
    "procurement-events": "/v1/report/procurement-events",
    "upload-events": "/v1/report/upload-events",
    "error-summary": "/v1/report/error-summary",
}
EVENT_NAMESPACE = uuid.UUID("8f0b7c04-026b-49b8-9a71-97ea43650218")


@dataclass(frozen=True, slots=True)
class EventDefinition:
    event_type: str
    endpoint: str
    priority: int = 1
    retention_days: int = 30
    allowed_fields: tuple[str, ...] = ()
    dedupe_fields: tuple[str, ...] = ()
    aggregation: str = "event"

    def __post_init__(self) -> None:
        if self.endpoint not in REPORT_ENDPOINTS:
            raise ValueError(f"未知运营上报接口：{self.endpoint}")
        if not self.event_type.strip():
            raise ValueError("event_type 不能为空")
        if not 0 <= int(self.priority) <= 2:
            raise ValueError("priority 必须是 0、1 或 2")
        if self.aggregation not in {"event", "latest", "counter"}:
            raise ValueError("aggregation 必须是 event、latest 或 counter")


@dataclass(frozen=True, slots=True)
class AnalyticsEvent:
    event_id: str
    event_type: str
    schema_version: int
    endpoint: str
    occurred_at: str
    device_ref: str
    store_ref: str
    batch_id: str
    payload: dict[str, Any]
    priority: int
    retention_days: int
    dedupe_key: str
    business_key: str
    revision: int
    aggregation: str
    size_bytes: int

    def envelope(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "occurred_at": self.occurred_at,
            "device_ref": self.device_ref,
            "store_ref": self.store_ref,
            "batch_id": self.batch_id,
            "revision": self.revision,
            "payload": self.payload,
        }


class AnalyticsAdapter:
    """Module-owned event contract.

    Adapters only project and validate events. Network, outbox and retry logic
    live in the central analytics services.
    """

    module_key = ""
    schema_version = 1
    definitions: tuple[EventDefinition, ...] = ()

    def event_types(self) -> tuple[EventDefinition, ...]:
        return self.definitions

    def definition(self, event_type: str) -> EventDefinition:
        for item in self.definitions:
            if item.event_type == event_type:
                return item
        raise ValueError(
            f"模块 {self.module_key or self.__class__.__name__} "
            f"未定义运营事件：{event_type}"
        )

    def should_emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        observed: bool = True,
    ) -> bool:
        self.definition(event_type)
        return bool(observed)

    def build_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None,
        *,
        device_ref: str,
        store_ref: str = "",
        batch_id: str = "",
        occurred_at: str | None = None,
        observed: bool = True,
    ) -> AnalyticsEvent | None:
        definition = self.definition(event_type)
        source = payload if isinstance(payload, dict) else {}
        if not self.should_emit(
            event_type,
            source,
            observed=observed,
        ):
            return None
        clean = sanitize_payload(
            source,
            allowed_fields=(
                definition.allowed_fields
                if definition.allowed_fields
                else None
            ),
        )
        reference = str(store_ref or source.get("store_ref") or "").strip()
        stamp = occurred_at or datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        batch = str(batch_id or uuid.uuid4().hex)
        dedupe_values = {
            "module": self.module_key,
            "event_type": event_type,
            "device_ref": str(device_ref),
            "store_ref": reference,
            **{
                field: clean.get(field)
                for field in definition.dedupe_fields
            },
        }
        business_key = json.dumps(
            dedupe_values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_digest = hashlib.sha256(
            json.dumps(
                clean,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        revision = int(time.time_ns())
        if definition.aggregation in {"latest", "counter"}:
            dedupe_key = f"{business_key}:{stamp}:{payload_digest}"
        else:
            dedupe_key = f"{business_key}:{payload_digest}"
        event_id = str(
            uuid.uuid5(
                EVENT_NAMESPACE,
                dedupe_key,
            )
        )
        return AnalyticsEvent(
            event_id=event_id,
            event_type=event_type,
            schema_version=int(self.schema_version),
            endpoint=definition.endpoint,
            occurred_at=stamp,
            device_ref=str(device_ref),
            store_ref=reference,
            batch_id=batch,
            payload=clean,
            priority=int(definition.priority),
            retention_days=int(definition.retention_days),
            dedupe_key=dedupe_key,
            business_key=business_key,
            revision=revision,
            aggregation=definition.aggregation,
            size_bytes=payload_size(clean),
        )


class AnalyticsRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AnalyticsAdapter] = {}
        self._lock = threading.RLock()
        self._frozen = False

    def register(
        self,
        adapter: AnalyticsAdapter,
        *,
        replace: bool = False,
    ) -> None:
        module_key = str(adapter.module_key or "").strip()
        if not module_key:
            raise ValueError("AnalyticsAdapter.module_key 不能为空")
        if not adapter.event_types():
            raise ValueError(f"模块 {module_key} 没有标准运营事件")
        with self._lock:
            if self._frozen and not replace:
                raise RuntimeError("运营事件注册表已冻结")
            if module_key in self._adapters and not replace:
                raise ValueError(f"运营适配器重复注册：{module_key}")
            self._adapters[module_key] = adapter

    def get(self, module_key: str) -> AnalyticsAdapter:
        with self._lock:
            try:
                return self._adapters[str(module_key)]
            except KeyError as exc:
                raise KeyError(f"业务模块缺少运营上报适配器：{module_key}") from exc

    def module_keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._adapters))

    def validate_module_coverage(
        self,
        required_module_keys: set[str] | tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        required = {str(value) for value in required_module_keys if value}
        missing = tuple(sorted(required.difference(self._adapters)))
        if missing:
            raise AssertionError(
                "业务模块缺少运营上报适配器："
                + ", ".join(missing)
            )
        return tuple(sorted(required))

    def freeze(self) -> None:
        with self._lock:
            self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen


registry = AnalyticsRegistry()
