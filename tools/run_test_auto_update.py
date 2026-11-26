"""Run the validated test-machine startup update against one installation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weimi_manager.runtime_logging import (  # noqa: E402
    configure_runtime_logging,
)
from weimi_manager.startup_update import run_startup_update  # noqa: E402
from weimi_manager.update_policy import (  # noqa: E402
    DEFAULT_TEST_INSTALL_DIR,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TEST_INSTALL_DIR,
    )
    args = parser.parse_args(argv)
    target = args.target.expanduser().resolve()
    if not (target / "LangLang.exe").is_file():
        raise FileNotFoundError(target / "LangLang.exe")
    manager = configure_runtime_logging(target)
    should_exit = run_startup_update(
        project_dir=target,
        force=True,
        allowed_test_install_dir=target,
    )
    print(
        json.dumps(
            {
                "target": str(target),
                "run_id": manager.run_id,
                "updater_started": should_exit,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
