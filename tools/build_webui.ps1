$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root "webui")
try {
    npm ci
    npm run build
}
finally {
    Pop-Location
}

Push-Location $root
try {
    & ".venv\Scripts\python.exe" -m PyInstaller --noconfirm "LangLangWebUI.spec"
}
finally {
    Pop-Location
}
