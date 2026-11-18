param(
    [string]$Python = "D:\GPT\langlang-1.7.0-pyside\.venv311\Scripts\python.exe",
    [string]$OutputRoot = "D:\GPT\builds\CardKeyGenerator",
    [string]$ClientConfig = "D:\GPT\builds\1.8.4\dist\LangLang\_internal\assets\auth_config.json"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Resolve-Path -LiteralPath $Python).Path
$ClientConfig = (Resolve-Path -LiteralPath $ClientConfig).Path
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$Dist = Join-Path $OutputRoot "dist"
$Work = Join-Path $OutputRoot "work"
$BuildLog = Join-Path $OutputRoot "build.log"
$Icon = (Resolve-Path -LiteralPath (Join-Path $Root "assets\icon.ico")).Path
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$OldPath = $env:PATH
$OldPythonPath = $env:PYTHONPATH
try {
    $PythonBin = Split-Path -Parent $Python
    $env:PATH = "$PythonBin;C:\Windows\System32;C:\Windows\System32\WindowsPowerShell\v1.0;C:\Windows"
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Push-Location $Root
    try {
        & $Python -m pytest tests\test_card_generator.py -q
        if ($LASTEXITCODE -ne 0) {
            throw "card generator tests failed: $LASTEXITCODE"
        }
        & $Python -m compileall -q weimi_manager tests tools
        if ($LASTEXITCODE -ne 0) {
            throw "compileall failed: $LASTEXITCODE"
        }
        & $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
            --name "CardKeyGenerator" `
            --icon "$Icon" `
            --distpath $Dist `
            --workpath $Work `
            --specpath $OutputRoot `
            --add-data "$ClientConfig;assets" `
            card_generator_launcher.py 2>&1 |
            Tee-Object -FilePath $BuildLog
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller failed: $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    $Exe = Join-Path $Dist "CardKeyGenerator.exe"
    if (!(Test-Path -LiteralPath $Exe)) {
        throw "Build completed without CardKeyGenerator.exe"
    }
    $Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $Report = [ordered]@{
        name = "LangLang Card Key Generator"
        version = (& $Python -c "from weimi_manager.card_generator import APP_VERSION; print(APP_VERSION)").Trim()
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
