#requires -Version 7.2
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot 'stop.ps1') -KeepDatabase
Push-Location -LiteralPath (Join-Path $taskRoot 'backend')
try {
    & (Join-Path $taskRoot '.tools/Scripts/uv.exe') run --locked python ../scripts/backup_restore.py backup
    if ($LASTEXITCODE -ne 0) { throw 'Cold backup did not pass; partial archive preserved.' }
} finally { Pop-Location }
# Services intentionally remain stopped until the operator starts them.
