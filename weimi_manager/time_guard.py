from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Iterable

import requests


DEFAULT_TIME_SOURCES = (
    "https://www.baidu.com/",
    "https://www.qq.com/",
    "https://www.taobao.com/",
)


@dataclass(frozen=True)
class NetworkTime:
    now: datetime
    source: str
    trusted: bool
    local_skew_seconds: float


class NetworkTimeGuard:
    """Cross-check the Windows clock against several Chinese public sites."""

    def __init__(
        self,
        sources: Iterable[str] = DEFAULT_TIME_SOURCES,
        timeout: float = 5.0,
        max_skew_seconds: float = 600.0,
    ) -> None:
        self.sources = tuple(sources)
        self.timeout = timeout
        self.max_skew_seconds = max_skew_seconds

    def _read_source(self, source: str) -> datetime | None:
        try:
            response = requests.get(
                source,
                timeout=self.timeout,
                allow_redirects=True,
                stream=True,
            )
            try:
                header = response.headers.get("Date")
            finally:
                response.close()
            if not header:
                return None
            parsed = parsedate_to_datetime(header)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (requests.RequestException, TypeError, ValueError, OverflowError):
            return None

    def sample(self) -> NetworkTime | None:
        values = [
            value
            for source in self.sources
            for value in [self._read_source(source)]
            if value is not None
        ]
        if not values:
            return None
        timestamps = sorted(value.timestamp() for value in values)
        median_timestamp = statistics.median(timestamps)
        median = datetime.fromtimestamp(median_timestamp, tz=UTC)
        local = datetime.now(UTC)
        skew = abs((local - median).total_seconds())
        return NetworkTime(
            now=median,
            source=f"{len(values)} 个国内时间源",
            trusted=len(values) >= 2 or skew <= self.max_skew_seconds,
            local_skew_seconds=skew,
        )
