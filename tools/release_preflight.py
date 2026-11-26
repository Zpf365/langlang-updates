"""Run mandatory diagnostics review and regression checks before release."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=ROOT / "artifacts" / "diagnostic-reports",
    )
    parser.add_argument(
        "--skip-candidate-verification",
        action="store_true",
    )
    args = parser.parse_args(argv)
    diagnostic_command = [
        sys.executable,
        str(ROOT / "tools" / "fetch_diagnostic_reports.py"),
        "--output",
        str(ROOT / "release" / "DIAGNOSTIC_REVIEW.md"),
        "--raw-dir",
        str(args.raw_dir),
    ]
    if args.data_dir:
        diagnostic_command.extend(
            ["--data-dir", str(args.data_dir)]
        )
    # Existing tester reports must be reviewed before every new package.
    run(diagnostic_command)
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
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
        ]
    )
    if not args.skip_candidate_verification:
        run(
            [
                sys.executable,
                str(ROOT / "tools" / "verify_stable_release.py"),
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
