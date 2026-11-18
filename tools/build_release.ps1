param(
    [string]$Python = "3.12"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

uv run --python $Python --with pyinstaller pyinstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "LangLang" `
    --icon "assets/icon.ico" `
    --add-data "assets;assets" `
    --add-data "update_manifest.json;." `
    --hidden-import playwright.sync_api `
    --hidden-import playwright.async_api `
    --collect-submodules playwright `
    "launcher.py"

Copy-Item -LiteralPath (Join-Path $ProjectRoot "PACKAGE-INSTRUCTIONS.txt") `
    -Destination (Join-Path $ProjectRoot "dist\LangLang\PACKAGE-INSTRUCTIONS.txt") `
    -Force

Write-Output "Build output: $ProjectRoot\dist\LangLang"
