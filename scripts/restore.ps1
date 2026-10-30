#requires -Version 7.2
param([Parameter(Mandatory=$true)][string]$BackupId)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath (Join-Path $taskRoot 'backend')
try {
    & (Join-Path $taskRoot '.tools/Scripts/uv.exe') run --locked python ../scripts/backup_restore.py restore --backup-id $BackupId
    if ($LASTEXITCODE -ne 0) { throw 'Isolated restore did not pass; original data preserved.' }
} finally { Pop-Location }
