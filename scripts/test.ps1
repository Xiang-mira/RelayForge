#requires -Version 7.2
param([switch]$Postgres, [switch]$Docker, [ValidatePattern('^[a-zA-Z0-9_-]+\.xml$')][string]$ReportName)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath (Join-Path $taskRoot 'backend')
try {
    $env:UV_CACHE_DIR = Join-Path $taskRoot '.local\cache\uv'
    if ($Postgres) { $env:RELAYFORGE_TEST_POSTGRES='1' }
    $taskPytestArgs=@('run','--locked','pytest','--tb=short')
    if ($ReportName) {
        $taskReportDirectory = Join-Path $taskRoot '.local\reports'
        New-Item -ItemType Directory -Path $taskReportDirectory -Force | Out-Null
        $taskPytestArgs += @('--junitxml', (Join-Path $taskReportDirectory $ReportName))
    }
    if (!$Postgres -and !$Docker) {$taskPytestArgs += @('-m','not postgres and not docker')}
    elseif (!$Postgres) {$taskPytestArgs += @('-m','not postgres')}
    elseif (!$Docker) {$taskPytestArgs += @('-m','not docker')}
    & (Join-Path $taskRoot '.tools\Scripts\uv.exe') @taskPytestArgs
    if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
} finally { Pop-Location }
Push-Location -LiteralPath (Join-Path $taskRoot 'frontend')
try { & npm run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' } } finally { Pop-Location }
if (!$Postgres) { Write-Output 'PostgreSQL tests excluded; related gates remain unverified.' }
if (!$Docker) { Write-Output 'Docker tests excluded; related gates remain unverified.' }
