from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..db import Database
from .adapter import AnalyticsEvent
from .api import AnalyticsApiClient, AnalyticsTransportError
from .outbox import AnalyticsOutbox


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    enabled: bool = False
    base_url: str = ""
    poll_seconds: float = 5.0
    debounce_seconds: float = 4.0
    batch_limit: int = 100
    batch_bytes: int = 256 * 1024
    timeout_seconds: int = 15
    max_queue_bytes: int = 50 * 1024 * 1024


class AnalyticsReporter:
    _instances: dict[str, "AnalyticsReporter"] = {}
    _instances_lock = threading.Lock()

    def __init__(
        self,
        db: Database,
        config: AnalyticsConfig,
        token_provider: Callable[[], str],
        *,
        api_client: AnalyticsApiClient | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.outbox = AnalyticsOutbox(
            db,
            max_queue_bytes=config.max_queue_bytes,
        )
        self.api_client = api_client
        if (
            self.api_client is None
            and config.enabled
            and config.base_url
        ):
            self.api_client = AnalyticsApiClient(
                config.base_url,
                token_provider,
                timeout_seconds=config.timeout_seconds,
            )
        self.token_provider = token_provider
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=10000)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._last_error_category = ""
        self._last_flush_at = 0.0
        self._persisted_count = 0
        self._sent_count = 0

    @classmethod
    def get_or_create(
        cls,
        db: Database,
        config: AnalyticsConfig,
        token_provider: Callable[[], str],
    ) -> "AnalyticsReporter":
        key = str(Path(db.path).resolve()).casefold()
        with cls._instances_lock:
            current = cls._instances.get(key)
            if current is None:
                current = cls(db, config, token_provider)
                cls._instances[key] = current
            return current

    def emit(self, event: AnalyticsEvent) -> bool:
        if not self.config.enabled:
            return False
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            return False
        self._wake.set()
        self.start()
        return True

    def post(self, task: Callable[[], None]) -> bool:
        if not self.config.enabled:
            return False
        try:
            self._queue.put_nowait(task)
        except queue.Full:
            return False
        self._wake.set()
        self.start()
        return True

    def start(self) -> None:
        if not self.config.enabled:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name=f"analytics-reporter-{id(self):x}",
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def _worker(self) -> None:
        next_prune = 0.0
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=self.config.poll_seconds)
            except queue.Empty:
                item = None
            if self._stop.is_set():
                return
            if item is not None:
                pending = [item]
                while len(pending) < 1000:
                    try:
                        pending.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                for event in pending:
                    if callable(event):
                        try:
                            event()
                        except Exception:
                            self._last_error_category = "builder_error"
                        continue
                    try:
                        if self.outbox.enqueue(event):
                            self._persisted_count += 1
                    except Exception:
                        # Disk/outbox failures stay internal and cannot reach UI.
                        self._last_error_category = "outbox_write"
                if pending:
                    self._stop.wait(self.config.debounce_seconds)
            try:
                self.flush_once()
            except Exception as exc:
                self._last_error_category = type(exc).__name__[:80]
            now = time.monotonic()
            if now >= next_prune:
                next_prune = now + 300
                try:
                    self.outbox.prune()
                except Exception:
                    self._last_error_category = "outbox_prune"

    def flush_once(self) -> dict[str, int]:
        if (
            not self.config.enabled
            or self.api_client is None
            or time.monotonic() < self._circuit_open_until
        ):
            return {"sent": 0, "released": 0}
        rows = self.outbox.pending_batch(
            limit=self.config.batch_limit,
            max_bytes=self.config.batch_bytes,
        )
        if not rows:
            return {"sent": 0, "released": 0}
        event_ids = [str(row["event_id"]) for row in rows]
        self.outbox.claim(event_ids)
        groups: dict[str, list[dict]] = defaultdict(list)
        endpoint_by_id: dict[str, str] = {}
        if getattr(self.api_client, "dispatcher_mode", False):
            groups[""].extend(
                self.outbox.event_envelope(row)
                for row in rows
            )
        else:
            for row in rows:
                endpoint = str(row.get("endpoint") or "")
                endpoint_key = endpoint.rsplit("/", 1)[-1]
                groups[endpoint_key].append(self.outbox.event_envelope(row))
                endpoint_by_id[str(row["event_id"])] = endpoint_key
        sent = 0
        released = 0
        try:
            for endpoint_key, events in groups.items():
                response = self.api_client.send(
                    endpoint_key,
                    events,
                    batch_id=str(rows[0].get("batch_id") or ""),
                )
                accepted = set(response.accepted_event_ids)
                rejected = set(response.rejected_event_ids)
                accepted_ids = [
                    str(item.get("event_id"))
                    for item in events
                    if str(item.get("event_id")) in accepted
                ]
                rejected_ids = [
                    str(item.get("event_id"))
                    for item in events
                    if str(item.get("event_id")) in rejected
                ]
                if accepted_ids:
                    self.outbox.mark_sent(accepted_ids)
                    sent += len(accepted_ids)
                    self._sent_count += len(accepted_ids)
                if rejected_ids:
                    self.outbox.drop(
                        rejected_ids,
                        error_category="server_rejected",
                    )
                unacknowledged = [
                    row
                    for row in rows
                    if str(row.get("event_id"))
                    in {
                        str(item.get("event_id"))
                        for item in events
                    }
                    and str(row.get("event_id"))
                    not in accepted
                    and str(row.get("event_id")) not in rejected
                ]
                if unacknowledged:
                    self.outbox.release(
                        unacknowledged,
                        error_category="ack_missing",
                    )
                    released += len(unacknowledged)
            self._consecutive_failures = 0
            self._last_error_category = ""
        except AnalyticsTransportError as exc:
            self.outbox.release(rows, error_category=exc.category)
            released += len(rows)
            self._record_failure(exc.category)
        except Exception:
            self.outbox.release(rows, error_category="reporter_error")
            released += len(rows)
            self._record_failure("reporter_error")
        self._last_flush_at = time.time()
        return {"sent": sent, "released": released}

    def _record_failure(self, category: str) -> None:
        self._consecutive_failures += 1
        self._last_error_category = str(category)[:120]
        if self._consecutive_failures >= 5:
            self._circuit_open_until = time.monotonic() + 600

    def flush_now(self) -> dict[str, int]:
        with self._lock:
            return self.flush_once()

    def status(self) -> dict[str, object]:
        thread = self._thread
        return {
            "enabled": self.config.enabled,
            "thread_alive": bool(thread and thread.is_alive()),
            "queued": self._queue.qsize(),
            "persisted": self._persisted_count,
            "sent": self._sent_count,
            "consecutive_failures": self._consecutive_failures,
            "circuit_open": time.monotonic() < self._circuit_open_until,
            "last_error_category": self._last_error_category,
            "last_flush_at": self._last_flush_at,
        }

    @classmethod
    def reset_singletons_for_tests(cls) -> None:
        with cls._instances_lock:
            instances = list(cls._instances.values())
            cls._instances.clear()
        for instance in instances:
            instance.stop()

    @classmethod
    def discard_for_database(cls, db: Database) -> None:
        key = str(Path(db.path).resolve()).casefold()
        with cls._instances_lock:
            instance = cls._instances.pop(key, None)
        if instance is not None:
            instance.stop()
