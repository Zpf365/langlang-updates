# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

hiddenimports = ['playwright.sync_api', 'playwright.async_api']
hiddenimports += collect_submodules('playwright')
rapid_datas, rapid_binaries, rapid_hidden = collect_all('rapidocr_onnxruntime')
onnx_datas, onnx_binaries, onnx_hidden = collect_all('onnxruntime')
hiddenimports += rapid_hidden + onnx_hidden
automation_datas = rapid_datas + onnx_datas
automation_binaries = rapid_binaries + onnx_binaries

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

browser_root = Path('browser_runtime')
browser_datas = []
if browser_root.is_dir():
    for path in sorted(browser_root.rglob('*')):
        if not path.is_file():
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
        *browser_datas,
        *automation_datas,
        ('update_manifest.json', '.'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
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
