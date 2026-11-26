from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).parents[1]


def _run_launcher(arguments: list[str], calls: list[str]) -> int:
    startup = ModuleType("weimi_manager.startup_update")
    startup.run_startup_update = lambda: calls.append("startup") or False
    webui_main = ModuleType("weimi_manager.webui.main")
    webui_main.main = lambda: calls.append("webui") or 0
    legacy_main = ModuleType("weimi_manager.main")
    legacy_main.main = lambda: calls.append("legacy") or 0
    with (
        patch.dict(
            sys.modules,
            {
                "weimi_manager.startup_update": startup,
                "weimi_manager.webui.main": webui_main,
                "weimi_manager.main": legacy_main,
            },
        ),
        patch.object(sys, "argv", ["launcher.py", *arguments]),
    ):
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(str(ROOT / "launcher.py"), run_name="__main__")
    return int(raised.value.code or 0)


def test_launcher_defaults_to_webui() -> None:
    calls: list[str] = []

    assert _run_launcher([], calls) == 0
    assert calls == ["startup", "webui"]


def test_launcher_never_imports_legacy_ui() -> None:
    calls: list[str] = []

    assert _run_launcher([], calls) == 0
    assert calls == ["startup", "webui"]


def test_acceptance_saved_account_requires_isolated_candidate_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from weimi_manager.webui.main import _acceptance_saved_account

    base = SimpleNamespace(data_dir=tmp_path / "normal" / "LangLang" / "data")
    monkeypatch.delenv("LANGLANG_ACCEPTANCE_MODE", raising=False)
    with pytest.raises(RuntimeError, match="ACCEPTANCE_MODE"):
        _acceptance_saved_account(
            ["--acceptance-saved-account", "zpfly"],
            base,
        )
    monkeypatch.setenv("LANGLANG_ACCEPTANCE_MODE", "candidate-exe")
    with pytest.raises(RuntimeError, match="candidate-exe"):
        _acceptance_saved_account(
            ["--acceptance-saved-account", "zpfly"],
            base,
        )
    isolated = SimpleNamespace(
        data_dir=tmp_path / "candidate-exe" / "LangLang" / "data"
    )
    assert _acceptance_saved_account(
        ["--acceptance-saved-account", "zpfly"],
        isolated,
    ) == "zpfly"
