"""Read the current stable update manifest without changing the installation."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.updater import (  # noqa: E402
    DEFAULT_UPDATE_SOURCE,
    _github_manifest_via_api,
    check_for_update,
)


def main() -> int:
    try:
        api_manifest = _github_manifest_via_api(DEFAULT_UPDATE_SOURCE)
        api_status = "ok"
    except Exception as exc:
        api_manifest = {}
        api_status = f"{type(exc).__name__}: {exc}"
    info = check_for_update()
    print(
        json.dumps(
            {
                "api_version": api_manifest.get("version"),
                "api_channel": api_manifest.get("channel"),
                "api_prerelease": api_manifest.get("prerelease"),
                "api_status": api_status,
                "check_version": info.latest_version,
                "check_channel": info.channel,
                "check_prerelease": info.prerelease,
                "source": info.source,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
