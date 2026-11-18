$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$PythonRoot = Get-ChildItem -LiteralPath "D:\" -Directory |
    Where-Object { $_.Name -like "2.0*" } |
    Select-Object -First 1
if ($null -eq $PythonRoot) {
    throw "Python 3.11 runtime directory not found"
}
$PythonDir = Join-Path $PythonRoot.FullName ".python311"
$PythonExe = Join-Path $PythonDir "python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

$BrowserRoot = Join-Path $ProjectRoot "browser_runtime"
$BrowserExe = Get-ChildItem -LiteralPath $BrowserRoot `
    -Recurse -File -Filter "chrome.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Directory.Name -in @("chrome-win", "chrome-win64") } |
    Select-Object -First 1
if ($null -eq $BrowserExe) {
    $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
    & $PythonExe -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "Playwright Chromium installation failed"
    }
    $BrowserExe = Get-ChildItem -LiteralPath $BrowserRoot `
        -Recurse -File -Filter "chrome.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -in @("chrome-win", "chrome-win64") } |
        Select-Object -First 1
}
if ($null -eq $BrowserExe) {
    throw "Bundled Chromium executable not found under $BrowserRoot"
}

$env:PATH = "$PythonDir;$PythonDir\Scripts;C:\Windows\System32;C:\Windows"
Remove-Item Env:QT_PLUGIN_PATH -ErrorAction SilentlyContinue
Remove-Item Env:QML2_IMPORT_PATH -ErrorAction SilentlyContinue
$env:LANGLANG_INCLUDE_TEST_ACCOUNTS = "0"

& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    "LangLang.spec"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath (Join-Path $ProjectRoot "PACKAGE-INSTRUCTIONS.txt") `
    -Destination (Join-Path $ProjectRoot "dist\LangLang\PACKAGE-INSTRUCTIONS.txt") `
    -Force

Write-Output "Build output: $ProjectRoot\dist\LangLang"
