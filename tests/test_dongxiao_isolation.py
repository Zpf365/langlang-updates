from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def test_default_application_and_database_do_not_load_dongxiao() -> None:
    environment = os.environ.copy()
    environment.pop("LANGLANG_ENABLE_DONGXIAO_ISOLATED", None)
    script = """
import sqlite3
import sys
import tempfile
from pathlib import Path

from weimi_manager.db import Database

with tempfile.TemporaryDirectory() as temp_dir:
    path = Path(temp_dir) / "default.db"
    Database(path)
    conn = sqlite3.connect(path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert not any(name.startswith("dongxiao_") for name in tables)

for name in sys.modules:
    assert name != "weimi_manager.dongxiao"
    assert not name.startswith("weimi_manager.dongxiao.")
    assert name != "weimi_manager.sales_related"
    assert not name.startswith("weimi_manager.sales_related.")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_default_entrypoints_and_navigation_have_no_isolated_route() -> None:
    for relative in (
        "weimi_manager/ui/app.py",
        "weimi_manager/pyside/main_window.py",
        "weimi_manager/pyside/pages/__init__.py",
        "weimi_manager/webui/controller.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "sales_related" not in source
        assert "DongxiaoRuntime" not in source
        assert "dongxiao_page" not in source
        assert "dongxiao_runtime" not in source

    database_source = (ROOT / "weimi_manager/db.py").read_text(encoding="utf-8")
    tree = ast.parse(database_source)
    top_level_imports = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "weimi_manager.dongxiao.infrastructure.migrations" not in top_level_imports
    assert "weimi_manager.sales_related.migrations" not in top_level_imports
    assert 'os.environ.get("LANGLANG_ENABLE_DONGXIAO_ISOLATED") != "1"' in database_source


def test_isolated_tests_are_excluded_but_preserved() -> None:
    isolated_dir = ROOT / "tests" / "isolated_dongxiao"
    isolated_tests = sorted(isolated_dir.glob("test_*.py"))
    assert len(isolated_tests) >= 34
    assert (isolated_dir / "ISOLATION_README_CN.md").is_file()

    pytest_config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'addopts = "--ignore=tests/isolated_dongxiao"' in pytest_config

    for spec_name in ("LangLang.spec", "LangLangWebUI.spec"):
        spec = (ROOT / spec_name).read_text(encoding="utf-8")
        assert '"weimi_manager.dongxiao"' in spec or "'weimi_manager.dongxiao'" in spec
        assert (
            '"weimi_manager.sales_related"' in spec
            or "'weimi_manager.sales_related'" in spec
        )
        assert "excludes=isolated_excludes" in spec


def test_backup_contains_runtime_sources_for_non_destructive_restore() -> None:
    backup = ROOT / "isolated" / "dongxiao_backup_1_8_73"
    if not backup.is_dir():
        pytest.skip("legacy backup is not part of the clean release worktree")
    required = (
        "weimi_manager/dongxiao/application/runtime.py",
        "weimi_manager/dongxiao/infrastructure/migrations.py",
        "weimi_manager/sales_related/migrations.py",
        "weimi_manager/ui/dongxiao_page.py",
        "weimi_manager/ui/buyer_account_wizard.py",
        "weimi_manager/pyside/pages/sales_related.py",
        "tests/isolated_dongxiao/test_dongxiao_phase4_tasks.py",
    )
    for relative in required:
        assert (backup / relative).is_file(), relative
