param(
    [string]$Python = "3.12"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

uv run --python $Python --with pyinstaller pyinstaller `
    --noconfirm `
    --clean `
    "LangLang.spec"

Copy-Item -LiteralPath (Join-Path $ProjectRoot "PACKAGE-INSTRUCTIONS.txt") `
    -Destination (Join-Path $ProjectRoot "dist\LangLang\PACKAGE-INSTRUCTIONS.txt") `
    -Force

Write-Output "Build output: $ProjectRoot\dist\LangLang"
