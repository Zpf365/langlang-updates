from __future__ import annotations

import os
import uuid

import pytest

from weimi_manager.webui.clipboard import (
    read_clipboard_text,
    write_clipboard_text,
)
from weimi_manager.webui.controller import NextUiController
from weimi_manager.db import Database


@pytest.mark.skipif(os.name != "nt", reason="Windows clipboard test")
def test_native_clipboard_round_trip_and_controller_rpc(tmp_path) -> None:
    previous = read_clipboard_text()
    value = f"澜琅剪贴板测试-{uuid.uuid4().hex}"
    try:
        result = write_clipboard_text(value)
        assert result["written"] is True
        assert read_clipboard_text() == value

        database = Database(tmp_path / "clipboard.db")
        controller = NextUiController(database)
        rpc_value = f"订单号-{uuid.uuid4().hex}"
        result = controller.dispatch("clipboard.write", {"text": rpc_value})
        assert result["written"] is True
        assert read_clipboard_text() == rpc_value
    finally:
        write_clipboard_text(previous)
