from __future__ import annotations

from pathlib import Path

import pytest

from weimi_manager.webui.packaging import filter_windows_icu, is_bundled_windows_icu
from tools.generate_stable_release import (
    SOURCE_EXCLUDED_DIRS,
    SOURCE_ROOTS,
    verify_webui_assets,
    verify_webui_entry_source,
    verify_webui_program,
)


def test_root_icu_binaries_are_recognized() -> None:
    assert is_bundled_windows_icu("icuuc.dll")
    assert is_bundled_windows_icu("ICUIN.DLL")
    assert is_bundled_windows_icu("icudt78.dll")
    assert not is_bundled_windows_icu("browser_runtime/chrome/icuuc.dll")
    assert not is_bundled_windows_icu("Qt6Core.dll")


def test_packaging_filter_removes_icu_without_touching_other_binaries() -> None:
    entries = [
        ("icuuc.dll", r"C:\build-host\icuuc.dll", "BINARY"),
        ("icudt78.dll", r"C:\build-host\icudt78.dll", "BINARY"),
        ("PySide6/Qt6Core.dll", r"C:\venv\Qt6Core.dll", "BINARY"),
        ("browser_runtime/icuuc.dll", r"C:\browser\icuuc.dll", "BINARY"),
        ("product.dll", r"C:\app\product.dll", "BINARY"),
    ]

    filtered = filter_windows_icu(entries)

    assert filtered == entries[2:]


def test_webui_spec_applies_the_windows_icu_filter() -> None:
    spec = (Path(__file__).parents[1] / "LangLangWebUI.spec").read_text(encoding="utf-8")

    assert "from weimi_manager.webui.packaging import filter_windows_icu" in spec
    assert "a.binaries = filter_windows_icu(a.binaries)" in spec


def test_formal_webui_entry_and_specs_are_mandatory() -> None:
    verify_webui_entry_source()
    for name in ("LangLang.spec", "LangLangWebUI.spec"):
        spec = (Path(__file__).parents[1] / name).read_text(encoding="utf-8")
        assert "launcher.py" in spec
        assert "frontend_root" in spec
        assert "PySide6.QtWebEngineCore" in spec
        assert "PySide6.QtWebEngineWidgets" in spec
        assert "PySide6.QtWebChannel" in spec
        assert "PySide6.QtNetwork" in spec
        assert "weimi_manager.main" in spec
        assert "weimi_manager.ui.app" in spec
        assert "正式 WebUI 包禁止包含旧工作台模块" in spec


def test_release_verifier_rejects_program_without_webui_assets(
    tmp_path: Path,
) -> None:
    program = tmp_path / "LangLang"
    program.mkdir()

    with pytest.raises(RuntimeError, match="webui/dist/index.html"):
        verify_webui_assets(program)


def test_release_verifier_requires_all_key_webui_assets(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "LangLang" / "_internal" / "webui" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("ok", encoding="utf-8")
    for name in ("StoresPage", "OrdersParityPage", "ProductsPage"):
        (assets / f"{name}-test.js").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="autoRefresh"):
        verify_webui_assets(tmp_path / "LangLang")

    (assets / "autoRefresh-test.js").write_text("", encoding="utf-8")
    verify_webui_assets(tmp_path / "LangLang")


def test_desktop_shows_webview_before_load_and_reveals_after_finish() -> None:
    source = (
        Path(__file__).parents[1] / "weimi_manager" / "webui" / "desktop.py"
    ).read_text(encoding="utf-8")

    assert source.index("window.show()") < source.index("view.load(QUrl(server.entry_url))")
    assert "page.loadFinished.connect(reveal_loaded_page)" in source
    assert "window.raise_()" in source
    assert "window.activateWindow()" in source
    assert "QTimer.singleShot(120, view.update)" in source


def test_source_archive_includes_webui_without_node_modules() -> None:
    assert "webui" in SOURCE_ROOTS
    assert "node_modules" in SOURCE_EXCLUDED_DIRS
