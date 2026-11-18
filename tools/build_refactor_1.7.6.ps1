param(
    [string]$Python = "D:\2.0微店\.python311\python.exe",
    [string]$OutputRoot = "D:\GPT\builds\1.7.6",
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
$ClientConfigText = $null
if ($ClientConfig) {
    $ResolvedClientConfig = (Resolve-Path -LiteralPath $ClientConfig).Path
    $ClientConfigText = [IO.File]::ReadAllText($ResolvedClientConfig)
}

$OldPath = $env:PATH
$OldPythonPath = $env:PYTHONPATH
try {
    $PythonBin = Split-Path -Parent $Python
    $env:PATH = "$PythonBin;C:\Windows\System32;C:\Windows\System32\WindowsPowerShell\v1.0;C:\Windows"
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
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
    if ($null -ne $ClientConfigText) {
        $AssetDir = Join-Path $Dist "LangLang\_internal\assets"
        New-Item -ItemType Directory -Path $AssetDir -Force | Out-Null
        [IO.File]::WriteAllText(
            (Join-Path $AssetDir "auth_config.json"),
            $ClientConfigText,
            (New-Object System.Text.UTF8Encoding($false))
        )
    }
    $Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $Version = (& $Python -c "from weimi_manager import __version__; print(__version__)").Trim()
    $DisplayVersion = (& $Python -c "from weimi_manager import __display_version__; print(__display_version__)").Trim()
    $Report = [ordered]@{
        version = $Version
        display_name = $DisplayVersion
        ui = "1.6.63 Tkinter (original UI)"
        python = (& $Python --version)
        path = $env:PATH
        exe = $Exe
        sha256 = $Hash
        built_at = (Get-Date).ToString("o")
    }
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
}
