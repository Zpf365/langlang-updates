from __future__ import annotations

import ctypes
import os
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, TypeVar


STORE_REFRESH_MIN_WORKERS = 6
STORE_REFRESH_MAX_WORKERS = 12
STORE_REFRESH_FALLBACK_CPU_COUNT = 4
STORE_REFRESH_FALLBACK_MEMORY_MB = 2048

StoreT = TypeVar("StoreT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class StoreRefreshOutcome:
    store: Any
    result: Any = None
    error: BaseException | None = None


def _windows_available_memory_mb() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    return max(0, int(status.ullAvailPhys // (1024 * 1024)))


def detect_available_memory_mb() -> int:
    memory_mb = _windows_available_memory_mb()
    return (
        int(memory_mb)
        if memory_mb is not None
        else STORE_REFRESH_FALLBACK_MEMORY_MB
    )


def _hardware_budget(
    *,
    cpu_count: int,
    available_memory_mb: int,
) -> int:
    cpu = max(1, int(cpu_count))
    memory_mb = max(0, int(available_memory_mb))
    if cpu <= 3:
        cpu_budget = 6
    elif cpu <= 5:
        cpu_budget = 8
    elif cpu <= 7:
        cpu_budget = 10
    else:
        cpu_budget = 12

    if memory_mb < 512:
        memory_budget = 6
    elif memory_mb < 1024:
        memory_budget = 7
    elif memory_mb < 2048:
        memory_budget = 8
    elif memory_mb < 4096:
        memory_budget = 10
    else:
        memory_budget = 12
    return min(cpu_budget, memory_budget)


def detect_store_refresh_budget() -> int:
    budget = _hardware_budget(
        cpu_count=int(os.cpu_count() or STORE_REFRESH_FALLBACK_CPU_COUNT),
        available_memory_mb=detect_available_memory_mb(),
    )
    return max(
        STORE_REFRESH_MIN_WORKERS,
        min(STORE_REFRESH_MAX_WORKERS, budget),
    )


def configured_store_refresh_workers() -> int | None:
    raw = str(os.environ.get("WEIMI_STORE_REFRESH_WORKERS", "")).strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return max(
        STORE_REFRESH_MIN_WORKERS,
        min(STORE_REFRESH_MAX_WORKERS, value),
    )


def resolve_store_refresh_workers(
    store_count: int,
    *,
    configured: int | None = None,
    cpu_count: int | None = None,
    available_memory_mb: int | None = None,
) -> int:
    count = max(0, int(store_count))
    if count == 0:
        return 0

    if configured is not None:
        budget = int(configured)
    elif cpu_count is not None or available_memory_mb is not None:
        budget = _hardware_budget(
            cpu_count=int(cpu_count or STORE_REFRESH_FALLBACK_CPU_COUNT),
            available_memory_mb=int(
                available_memory_mb
                if available_memory_mb is not None
                else STORE_REFRESH_FALLBACK_MEMORY_MB
            ),
        )
    else:
        configured = configured_store_refresh_workers()
        budget = (
            int(configured)
            if configured is not None
            else detect_store_refresh_budget()
        )
    budget = max(
        STORE_REFRESH_MIN_WORKERS,
        min(STORE_REFRESH_MAX_WORKERS, budget),
    )
    return min(count, budget)


def run_store_refresh_queue(
    stores: Iterable[StoreT],
    *,
    max_workers: int,
    task: Callable[[StoreT], ResultT],
    should_stop: Callable[[], bool] = lambda: False,
    dispatch_lock: Any = None,
    on_dispatch: Callable[[StoreT], None] = lambda _store: None,
    on_cancel: Callable[[StoreT], None] = lambda _store: None,
    on_finish: Callable[[StoreRefreshOutcome], None] = lambda _outcome: None,
    thread_name_prefix: str = "store-refresh-pool",
) -> None:
    pending = deque(stores)
    if not pending:
        return
    worker_count = max(1, min(int(max_workers), len(pending)))
    in_flight: dict[Future[ResultT], StoreT] = {}

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix=thread_name_prefix,
    ) as executor:
        while pending or in_flight:
            while (
                pending
                and len(in_flight) < worker_count
                and not should_stop()
            ):
                lock_context = (
                    dispatch_lock
                    if dispatch_lock is not None
                    else nullcontext()
                )
                with lock_context:
                    if should_stop() or not pending:
                        break
                    store = pending.popleft()
                    on_dispatch(store)
                    future = executor.submit(task, store)
                    in_flight[future] = store

            if should_stop() and pending:
                while pending:
                    on_cancel(pending.popleft())

            if not in_flight:
                continue

            observed, _ = wait(
                in_flight,
                timeout=0.1,
                return_when=FIRST_COMPLETED,
            )
            for future in observed:
                store = in_flight.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    on_finish(
                        StoreRefreshOutcome(
                            store=store,
                            error=exc,
                        )
                    )
                else:
                    on_finish(
                        StoreRefreshOutcome(
                            store=store,
                            result=result,
                        )
                    )
