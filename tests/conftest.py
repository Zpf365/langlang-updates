from __future__ import annotations

import gc
import os

import pytest


os.environ.setdefault("LANGLANG_DISABLE_UI_WATCHDOG", "1")


@pytest.fixture(autouse=True)
def _collect_native_wrapper_cycles() -> None:
    """Keep Tk/PySide wrapper cycles in the pytest main thread on Windows."""
    yield
    gc.collect()
