from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_tester_channel(resource_dir: Path) -> dict[str, Any]:
    path = resource_dir / "assets" / "tester_channel.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}
