from __future__ import annotations

from pathlib import Path

from weimi_manager import browser_runtime


def test_browser_directory_override_selects_bundled_chromium(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "chromium-1234" / "chrome-win" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test")
    monkeypatch.setenv("LANGLANG_BROWSER_DIR", str(tmp_path))
    browser_runtime.reset_browser_runtime_cache()

    assert browser_runtime.find_browser_executable() == executable.resolve()
    options = browser_runtime.browser_launch_options(headless=False)
    assert options["executable_path"] == str(executable.resolve())
    assert "channel" not in options


def test_packaged_runtime_fails_clearly_without_browser(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LANGLANG_BROWSER_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(browser_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(browser_runtime.sys, "executable", str(tmp_path / "LangLang.exe"))
    monkeypatch.setattr(browser_runtime.sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(
        browser_runtime,
        "_search_roots",
        lambda: [tmp_path / "missing"],
    )
    browser_runtime.reset_browser_runtime_cache()

    try:
        browser_runtime.browser_launch_options(headless=True)
    except browser_runtime.BrowserRuntimeError as exc:
        assert "浏览器组件缺失" in str(exc)
    else:
        raise AssertionError("missing packed browser must fail")
