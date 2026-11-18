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

from weimi_manager.main import main  # noqa: E402

raise SystemExit(main())
