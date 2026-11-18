$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $ProjectRoot "dist\LangLang"
$TargetParent = "D:\WG"
$Target = Join-Path $TargetParent "LangLang"
$SourceExe = Join-Path $Source "LangLang.exe"

if (-not (Test-Path -LiteralPath $SourceExe)) {
    throw "Latest build not found: $SourceExe"
}
if ((Resolve-Path -LiteralPath $Source).Path -eq $Target) {
    throw "Source and target are identical"
}

$resolvedParent = (Resolve-Path -LiteralPath $TargetParent).Path
if ($resolvedParent.TrimEnd("\") -ne "D:\WG") {
    throw "Unexpected deployment parent: $resolvedParent"
}

Get-Process LangLang -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = Join-Path $TargetParent "LangLang.update-backup-$stamp"
if (Test-Path -LiteralPath $Target) {
    Move-Item -LiteralPath $Target -Destination $Backup
}

try {
    Copy-Item -LiteralPath $Source -Destination $Target -Recurse
} catch {
    if (Test-Path -LiteralPath $Target) {
        $failed = Join-Path $TargetParent "LangLang.deploy-failed-$stamp"
        Move-Item -LiteralPath $Target -Destination $failed
    }
    if (Test-Path -LiteralPath $Backup) {
        Move-Item -LiteralPath $Backup -Destination $Target
    }
    throw
}

$exe = Join-Path $Target "LangLang.exe"
$hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
Start-Process -FilePath $exe -WorkingDirectory $Target

[pscustomobject]@{
    VersionSource = $Source
    DeployedTo = $Target
    Backup = if (Test-Path -LiteralPath $Backup) { $Backup } else { "" }
    ExecutableSHA256 = $hash
} | Format-List
