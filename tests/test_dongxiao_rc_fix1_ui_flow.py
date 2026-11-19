from __future__ import annotations

import sqlite3
from pathlib import Path

from weimi_manager.config import AppPaths
from weimi_manager.ui.app import MainWindow
from weimi_manager.ui.dongxiao_page import DongxiaoPage


def test_source_tk_flow_switches_all_dongxiao_pages_and_restarts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "isolated-data"
    monkeypatch.setenv("WEIMI_DATA_DIR", str(data_dir))
    monkeypatch.setenv("WEIMI_DISABLE_AUTO_BACKUP", "1")
    monkeypatch.setenv("WEIMI_DISABLE_AUTO_UPDATE", "1")

    first = MainWindow(AppPaths.discover())
    first.withdraw()
    runtime = None
    try:
        first.show_page("sales_related")
        first.update_idletasks()
        page = first.sales_related_page
        assert isinstance(page, DongxiaoPage)
        runtime = first.ensure_dongxiao_runtime()
        assert runtime is not None
        for key in page.section_buttons:
            page.section_buttons[key].invoke()
            first.update_idletasks()
            assert page._active_section == key
        health = runtime.scheduler_health()
        assert health["healthy"] is True
        assert health["idle"] is True

        conn = sqlite3.connect(AppPaths.discover().database)
        try:
            rows = conn.execute(
                """
                SELECT version
                FROM dongxiao_schema_migrations
                ORDER BY version
                """
            ).fetchall()
        finally:
            conn.close()
        assert [int(row[0]) for row in rows] == [1, 2, 3, 4, 5, 6]
    finally:
        if runtime is not None:
            runtime.stop()
        first.destroy()

    second = MainWindow(AppPaths.discover())
    second.withdraw()
    try:
        second.show_page("sales_related")
        second.update_idletasks()
        reopened = second.sales_related_page
        assert isinstance(reopened, DongxiaoPage)
        assert reopened.runtime is not None
        assert reopened.runtime.platform_writes_enabled() is False
    finally:
        if second.dongxiao_runtime is not None:
            second.dongxiao_runtime.stop()
        second.destroy()
