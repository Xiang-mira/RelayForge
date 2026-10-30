#requires -Version 7.2
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskEnv = Join-Path $taskRoot '.env'
if (Test-Path -LiteralPath $taskEnv) { Write-Output '.env exists; preserved.'; exit 0 }
$taskSecret = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).ToLowerInvariant()
$taskConfig = @"
MODEL_PROVIDER=mock
API_HOST=127.0.0.1
API_PORT=8000
POSTGRES_USER=relayforge
POSTGRES_DB=relayforge
POSTGRES_PASSWORD=$taskSecret
DATABASE_URL=postgresql+psycopg://relayforge:$taskSecret@127.0.0.1:5432/relayforge
LANGGRAPH_STRICT_MSGPACK=true
"@
$taskBytes = [System.Text.Encoding]::UTF8.GetBytes($taskConfig)
$taskFile = [System.IO.File]::Open($taskEnv, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
try { $taskFile.Write($taskBytes, 0, $taskBytes.Length) } finally { $taskFile.Dispose() }
Write-Output 'Created local .env with random database credentials; contents not displayed.'
