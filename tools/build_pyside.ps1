param(
    [string]$Python = "D:\2.0微店\.python311\python.exe",
    [string]$OutputRoot = "D:\GPT\builds\1.7.0",
    [string]$ClientConfig = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Resolve-Path -LiteralPath $Python).Path
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$Dist = Join-Path $OutputRoot "dist"
$Work = Join-Path $OutputRoot "work"
$BuildLog = Join-Path $OutputRoot "build.log"
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$OldPath = $env:PATH
$OldPythonPath = $env:PYTHONPATH
$OldQtPluginPath = $env:QT_PLUGIN_PATH
$OldQtPath = $env:QTDIR
try {
    $PythonBin = Split-Path -Parent $Python
    $env:PATH = "$PythonBin;C:\Windows\System32;C:\Windows\System32\WindowsPowerShell\v1.0;C:\Windows"
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:QT_PLUGIN_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:QTDIR -ErrorAction SilentlyContinue

    Push-Location $Root
    try {
        $env:LANGLANG_TEST_OUTPUT = Join-Path $OutputRoot "tests"
        & $Python tools\run_tests.py
        if ($LASTEXITCODE -ne 0) {
            throw "test suite failed: $LASTEXITCODE"
        }
        & $Python -m compileall -q weimi_manager tests tools
        if ($LASTEXITCODE -ne 0) {
            throw "compileall failed: $LASTEXITCODE"
        }
        $OldNativeErrorPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $Python -m PyInstaller --noconfirm --clean `
                --distpath $Dist --workpath $Work LangLang.spec 2>&1 |
                Tee-Object -FilePath $BuildLog
            $PyInstallerExit = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $OldNativeErrorPreference
        }
        if ($PyInstallerExit -ne 0) {
            throw "PyInstaller failed: $PyInstallerExit"
        }
    }
    finally {
        Pop-Location
    }

    $Exe = Join-Path $Dist "LangLang\LangLang.exe"
    if (!(Test-Path -LiteralPath $Exe)) {
        throw "Build completed without LangLang.exe"
    }
    if ($ClientConfig) {
        $ResolvedClientConfig = (Resolve-Path -LiteralPath $ClientConfig).Path
        $AssetDir = Join-Path $Dist "LangLang\_internal\assets"
        New-Item -ItemType Directory -Path $AssetDir -Force | Out-Null
        Copy-Item -LiteralPath $ResolvedClientConfig `
            -Destination (Join-Path $AssetDir "auth_config.json") -Force
    }
    $Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $Report = [ordered]@{
        version = "1.7.0"
        python = (& $Python --version)
        path = $env:PATH
        exe = $Exe
        sha256 = $Hash
        built_at = (Get-Date).ToString("o")
    }
    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
    $Report | ConvertTo-Json | Set-Content `
        -LiteralPath (Join-Path $OutputRoot "build-report.json") `
        -Encoding utf8
    $Report | Format-List
}
finally {
    $env:PATH = $OldPath
    if ($null -ne $OldPythonPath) {
        $env:PYTHONPATH = $OldPythonPath
    }
    if ($null -ne $OldQtPluginPath) {
        $env:QT_PLUGIN_PATH = $OldQtPluginPath
    }
    if ($null -ne $OldQtPath) {
        $env:QTDIR = $OldQtPath
    }
}
