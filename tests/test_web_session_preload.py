from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weimi_manager.web_session import WechatWebSession


class WebSessionPreloadTests(unittest.TestCase):
    def test_session_preloads_playwright_during_construction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "weimi_manager.web_session.preload_playwright"
            ) as preload:
                WechatWebSession(mock.Mock(), Path(tmp))

        preload.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
