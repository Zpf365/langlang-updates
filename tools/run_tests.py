from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("LANGLANG_TEST_OUTPUT", ROOT / "artifacts" / "tests"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    tcl_root = Path(sys.base_prefix) / "tcl"
    tcl_library = tcl_root / "tcl8.6"
    tk_library = tcl_root / "tk8.6"
    if tcl_library.is_dir():
        env["TCL_LIBRARY"] = str(tcl_library)
    if tk_library.is_dir():
        env["TK_LIBRARY"] = str(tk_library)
    for test in sorted((ROOT / "tests").glob("test_*.py")):
        started = time.monotonic()
        attempts = 0
        item: dict[str, object] = {}
        for attempt in range(1, 3):
            attempts = attempt
            try:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "-p",
                        "no:cacheprovider",
                        str(test),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    timeout=180,
                    env=env,
                )
                output = process.stdout.decode("utf-8", "replace") + process.stderr.decode(
                    "utf-8", "replace"
                )
                (OUT / f"{test.stem}.attempt{attempt}.log").write_text(
                    output,
                    encoding="utf-8",
                )
                item = {
                    "file": test.name,
                    "exit": process.returncode,
                    "ok": process.returncode == 0,
                    "attempts": attempts,
                    "seconds": round(time.monotonic() - started, 1),
                }
                if process.returncode == 0:
                    break
            except subprocess.TimeoutExpired:
                item = {
                    "file": test.name,
                    "ok": False,
                    "timeout": True,
                    "attempts": attempts,
                    "seconds": round(time.monotonic() - started, 1),
                }
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        (OUT / "test-results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
