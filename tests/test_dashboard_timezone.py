from __future__ import annotations

from datetime import datetime

from weimi_manager.dashboard_metrics import _parse_time


def test_aware_utc_timestamp_is_compared_in_local_time() -> None:
    value = "2026-11-18T23:30:00+00:00"

    parsed = _parse_time(value)

    expected = (
        datetime.fromisoformat(value)
        .astimezone()
        .replace(tzinfo=None)
    )
    assert parsed == expected
