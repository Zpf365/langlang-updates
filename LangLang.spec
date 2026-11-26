# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules
sys.path.insert(0, str(Path.cwd()))
from weimi_manager.webui.packaging import filter_windows_icu

hiddenimports = [
    'playwright.sync_api',
    'playwright.async_api',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel',
    'PySide6.QtNetwork',
    'weimi_manager.big_screen',
    'weimi_manager.dashboard_metrics',
    'weimi_manager.ui.reference_big_screen',
    'weimi_manager.ui.skeletons',
    'weimi_manager.ui.pages',
    'weimi_manager.ui.dialogs',
    'weimi_manager.ui.procurement_account_dialog',
]
hiddenimports += collect_submodules('playwright')
hiddenimports += collect_submodules('pywinauto')
hiddenimports += collect_submodules('comtypes')
rapid_datas, rapid_binaries, rapid_hidden = collect_all('rapidocr_onnxruntime')
onnx_datas, onnx_binaries, onnx_hidden = collect_all('onnxruntime')
hiddenimports += rapid_hidden + onnx_hidden
automation_datas = rapid_datas + onnx_datas
automation_binaries = rapid_binaries + onnx_binaries
isolated_excludes = [
    'weimi_manager.main',
    'weimi_manager.ui.app',
    'weimi_manager.dongxiao',
    'weimi_manager.sales_related',
    'weimi_manager.ui.dongxiao_page',
    'weimi_manager.ui.sales_related_page',
    'weimi_manager.ui.buyer_account_wizard',
    'weimi_manager.pyside.pages.sales_related',
]

public_asset_excludes = {'tester_channel.json'}
if os.environ.get('LANGLANG_INCLUDE_TEST_ACCOUNTS') != '1':
    public_asset_excludes.update({
        'tester_accounts.json',
        'universal_login.json',
    })
asset_root = Path('assets')
asset_datas = [
    (
        str(path),
        str(Path('assets') / path.relative_to(asset_root).parent),
    )
    for path in sorted(asset_root.rglob('*'))
    if path.is_file() and path.name not in public_asset_excludes
]

frontend_root = Path('webui') / 'dist'
if not (frontend_root / 'index.html').is_file():
    raise RuntimeError(
        '缺少 webui/dist；请先在 webui 目录运行 npm run build。'
    )
frontend_datas = [
    (
        str(path),
        str(Path('webui') / 'dist' / path.relative_to(frontend_root).parent),
    )
    for path in sorted(frontend_root.rglob('*'))
    if path.is_file()
]

browser_root = Path('browser_runtime')
browser_datas = []
if browser_root.is_dir():
    for path in sorted(browser_root.rglob('*')):
        if not path.is_file():
            continue
        if path.suffix.casefold() in {
            '.db',
            '.jsonl',
            '.log',
            '.sqlite',
            '.sqlite3',
        }:
            continue
        relative = path.relative_to(browser_root)
        if relative.parts and (
            relative.parts[0].startswith('chromium_headless_shell-')
            or relative.parts[0].startswith('winldd-')
            or relative.parts[0] == '.links'
        ):
            continue
        browser_datas.append(
            (
                str(path),
                str(Path('browser_runtime') / relative.parent),
            )
        )
if not any(
    destination.endswith('chrome-win')
    or destination.endswith('chrome-win64')
    or destination.endswith('browser_runtime')
    for _source, destination in browser_datas
):
    raise RuntimeError(
        '缺少 browser_runtime/chromium-*/chrome-win64/chrome.exe；'
        '请先运行 python -m playwright install chromium。'
    )


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=automation_binaries,
    datas=[
        *asset_datas,
        *frontend_datas,
        *browser_datas,
        *automation_datas,
        ('update_manifest.json', '.'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=isolated_excludes,
    noarchive=False,
    optimize=0,
)
for forbidden_module in ('weimi_manager.main', 'weimi_manager.ui.app'):
    if any(entry[0] == forbidden_module for entry in a.pure):
        raise RuntimeError(
            f'正式 WebUI 包禁止包含旧工作台模块：{forbidden_module}'
        )
a.binaries = [
    entry
    for entry in a.binaries
    if not (
        Path(entry[0]).name.lower().startswith('api-ms-win-')
        or Path(entry[0]).name.lower() == 'ucrtbase.dll'
    )
]
a.binaries = filter_windows_icu(a.binaries)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LangLang',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LangLang',
)
