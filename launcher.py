from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def automation_self_test(arguments: list[str]) -> int:
    image = Path(arguments[0])
    output = Path(arguments[1])
    log = output.with_suffix(".log")
    with log.open("w", encoding="utf-8") as handle:
        handle.write("starting\n")
        handle.flush()
        try:
            os.environ.setdefault("OMP_NUM_THREADS", "1")
            os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
            from weimi_manager.mobile_automation import ocr_image

            handle.write("imported\n")
            handle.flush()
            lines = ocr_image(image)
            handle.write(f"recognized={len(lines)}\n")
            output.write_text(
                json.dumps(lines, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            handle.write(traceback.format_exc())
            return 1
    return 0


if len(sys.argv) == 4 and sys.argv[1] == "--automation-self-test":
    raise SystemExit(automation_self_test(sys.argv[2:]))

from weimi_manager.runtime_logging import (  # noqa: E402
    configure_runtime_logging,
    log_event,
)

_runtime_log_root = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
configure_runtime_logging(_runtime_log_root)
log_event(
    "launcher.started",
    argv=[redacted if "token" not in redacted.casefold() else "[REDACTED]" for redacted in sys.argv[1:]],
    frozen=bool(getattr(sys, "frozen", False)),
)

if "--big-screen" in sys.argv[1:]:
    from weimi_manager.big_screen import main as big_screen_main

    raise SystemExit(big_screen_main(sys.argv[1:]))

from weimi_manager.startup_update import run_startup_update  # noqa: E402

if run_startup_update():
    raise SystemExit(0)

from weimi_manager.webui.main import main  # noqa: E402

raise SystemExit(main())
