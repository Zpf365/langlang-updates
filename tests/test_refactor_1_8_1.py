from __future__ import annotations

import unittest
from pathlib import Path

from weimi_manager import __display_version__, __version__

ROOT = Path(__file__).resolve().parent.parent


class Refactor181Tests(unittest.TestCase):
    def test_release_metadata_uses_requested_name(self) -> None:
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(?:\.\d+)?$")
        self.assertEqual(__display_version__, f"重构{__version__}")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', pyproject)
        self.assertIn("1.6.63 原界面重构版", pyproject)

    def test_default_entry_uses_webui(self) -> None:
        run_script = (ROOT / "run.bat").read_text(encoding="utf-8")
        launcher = (ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertIn("launcher.py", run_script)
        self.assertIn("weimi_manager.webui.main import main", launcher)
        self.assertNotIn("weimi_manager.main import main", launcher)
        self.assertNotIn("--legacy-ui", launcher)
        self.assertNotIn("pyside_launcher.py", run_script)

    def test_release_build_uses_webui_launcher_and_resources(self) -> None:
        for spec_name in ("LangLang.spec", "LangLangWebUI.spec"):
            spec = (ROOT / spec_name).read_text(encoding="utf-8")
            self.assertIn("launcher.py", spec)
            self.assertIn("webui", spec)
            self.assertIn("dist", spec)
            self.assertIn("PySide6.QtWebEngineCore", spec)
            self.assertIn("PySide6.QtWebEngineWidgets", spec)
            self.assertIn("PySide6.QtWebChannel", spec)
            self.assertIn("PySide6.QtNetwork", spec)
        spec = (ROOT / "LangLang.spec").read_text(encoding="utf-8")
        self.assertIn("LANGLANG_INCLUDE_TEST_ACCOUNTS", spec)
        self.assertIn("'tester_accounts.json'", spec)
        self.assertIn("'universal_login.json'", spec)


if __name__ == "__main__":
    unittest.main()
