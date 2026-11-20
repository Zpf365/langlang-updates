from __future__ import annotations

import unittest
from pathlib import Path

from weimi_manager import __display_version__, __version__

ROOT = Path(__file__).resolve().parent.parent


class Refactor181Tests(unittest.TestCase):
    def test_release_metadata_uses_requested_name(self) -> None:
        self.assertEqual(__version__, "1.8.54")
        self.assertEqual(__display_version__, "重构1.8.54")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "1.8.54"', pyproject)
        self.assertIn("1.6.63 原界面重构版", pyproject)

    def test_default_entry_uses_legacy_workspace_ui(self) -> None:
        run_script = (ROOT / "run.bat").read_text(encoding="utf-8")
        self.assertIn("launcher.py", run_script)
        self.assertNotIn("pyside_launcher.py", run_script)

    def test_release_build_uses_legacy_ui_launcher(self) -> None:
        spec = (ROOT / "LangLang.spec").read_text(encoding="utf-8")
        self.assertIn("['launcher.py']", spec)
        self.assertNotIn("['pyside_launcher.py']", spec)
        self.assertIn("LANGLANG_INCLUDE_TEST_ACCOUNTS", spec)
        self.assertIn("'tester_accounts.json'", spec)
        self.assertIn("'universal_login.json'", spec)


if __name__ == "__main__":
    unittest.main()
