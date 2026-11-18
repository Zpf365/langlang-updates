from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    process_memory_mb: float | None
    process_cpu_percent: float | None
    system_memory_percent: float | None


class ResourceMonitor:
    """低频读取进程资源，不参与上货调度，不阻塞业务线程。"""

    def __init__(self) -> None:
        self._process = None
        self._psutil = None
        try:
            import psutil

            self._psutil = psutil
            self._process = psutil.Process()
            self._process.cpu_percent(interval=None)
        except (ImportError, OSError):
            self._process = None
            self._psutil = None

    @property
    def available(self) -> bool:
        return self._process is not None and self._psutil is not None

    def snapshot(self) -> ResourceSnapshot:
        if not self.available:
            return ResourceSnapshot(None, None, None)
        try:
            memory = self._process.memory_info().rss / (1024 * 1024)
            cpu = self._process.cpu_percent(interval=None)
            system_memory = self._psutil.virtual_memory().percent
            return ResourceSnapshot(memory, cpu, system_memory)
        except (OSError, self._psutil.Error):
            return ResourceSnapshot(None, None, None)
