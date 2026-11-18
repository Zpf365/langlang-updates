"""Run mandatory diagnostics review and regression checks before release."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    # Existing tester reports must be reviewed before every new package.
    run(
        [
            sys.executable,
            str(ROOT / "tools" / "fetch_diagnostic_reports.py"),
        ]
    )
    run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-q",
        ]
    )
    run(
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "weimi_manager",
            "tests",
            "tools",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
