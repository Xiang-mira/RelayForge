#requires -Version 7.2
param([switch]$ApiOnly, [switch]$NoWorker)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
& (Join-Path $PSScriptRoot 'configure.ps1')
$taskPort = 8000
foreach ($taskLine in (Get-Content -LiteralPath '.env')) {
    if ($taskLine -match '^API_PORT=(\d+)$') { $taskPort = [int]$Matches[1] }
}
foreach ($taskCheckPort in @($taskPort, 5173)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $taskCheckPort -ErrorAction SilentlyContinue) {
        throw "Port $taskCheckPort is occupied. Stop its owner or update configuration first."
    }
}
$taskPython = Join-Path $taskRoot 'backend\.venv\Scripts\python.exe'
$taskVite = Join-Path $taskRoot 'frontend\node_modules\vite\bin\vite.js'
if (!(Test-Path -LiteralPath $taskPython) -or !(Test-Path -LiteralPath $taskVite)) {
    throw 'Project dependencies missing. Sync backend and run npm ci in frontend first.'
}
if (!$ApiOnly) {
    . (Join-Path $PSScriptRoot 'docker-env.ps1')
    & $taskDockerExecutable compose up -d --wait postgres
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed.' }
    & (Join-Path $PSScriptRoot 'migrate.ps1')
}
New-Item -ItemType Directory -Path '.local\logs' -Force | Out-Null
$taskPidFile = Join-Path $taskRoot '.local\processes.json'
if (Test-Path -LiteralPath $taskPidFile) { throw 'Process ledger exists. Run stop.ps1 to validate/stop prior owned processes first.' }
$taskOwned = @()
try {
    $taskApi = Start-Process -FilePath $taskPython -ArgumentList @('-m','uvicorn','relayforge.api.app:app','--host','127.0.0.1','--port',"$taskPort") -WorkingDirectory (Join-Path $taskRoot 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput '.local\logs\api.out.log' -RedirectStandardError '.local\logs\api.err.log'
    $taskOwned += [pscustomobject]@{Id=$taskApi.Id;Start=$taskApi.StartTime.ToUniversalTime().ToString('o');Role='api';Executable=$taskPython}
    $taskOwned | ConvertTo-Json -AsArray | Set-Content -LiteralPath $taskPidFile -Encoding utf8
    $taskNode = (Get-Command node -ErrorAction Stop).Source
    $taskWeb = Start-Process -FilePath $taskNode -ArgumentList @('"' + $taskVite + '"','--host','127.0.0.1') -WorkingDirectory (Join-Path $taskRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput '.local\logs\web.out.log' -RedirectStandardError '.local\logs\web.err.log'
    $taskOwned += [pscustomobject]@{Id=$taskWeb.Id;Start=$taskWeb.StartTime.ToUniversalTime().ToString('o');Role='web';Executable=$taskNode}
    $taskOwned | ConvertTo-Json -AsArray | Set-Content -LiteralPath $taskPidFile -Encoding utf8
    $taskReady = $false
    for ($taskAttempt=0; $taskAttempt -lt 30; $taskAttempt++) {
        if ($taskApi.HasExited -or $taskWeb.HasExited) { throw 'A service exited; inspect .local/logs.' }
        try {
            $null = Invoke-RestMethod -Uri "http://127.0.0.1:$taskPort/health/live" -TimeoutSec 2
            $taskResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/health/live' -TimeoutSec 2
            if ($taskResponse.StatusCode -eq 200) { $taskReady=$true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (!$taskReady) { throw 'API/frontend proxy startup timed out.' }
    if (!$ApiOnly) { $null = Invoke-RestMethod -Uri "http://127.0.0.1:$taskPort/health" -TimeoutSec 5 }
    if (!$ApiOnly -and !$NoWorker) {
        $taskWorker = Start-Process -FilePath $taskPython -ArgumentList @('-m','relayforge.supervisor') -WorkingDirectory (Join-Path $taskRoot 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput '.local\logs\worker.out.log' -RedirectStandardError '.local\logs\worker.err.log'
        $taskOwned += [pscustomobject]@{Id=$taskWorker.Id;Start=$taskWorker.StartTime.ToUniversalTime().ToString('o');Role='worker';Executable=$taskPython}
        $taskOwned | ConvertTo-Json -AsArray | Set-Content -LiteralPath $taskPidFile -Encoding utf8
        $taskWorkerReady = $false
        for ($taskAttempt=0; $taskAttempt -lt 20; $taskAttempt++) {
            if ($taskWorker.HasExited) { throw 'Worker exited during startup; inspect .local/logs/worker.err.log.' }
            $taskSignal = Invoke-RestMethod -Uri "http://127.0.0.1:$taskPort/api/worker" -TimeoutSec 3
            # A Windows venv python.exe redirects to a child base interpreter.
            # Validate the actual heartbeat process is this live launcher's child.
            $taskSignalProcess = if ($taskSignal.process_id) { Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$taskSignal.process_id)" } else { $null }
            $taskOwnedSignal = ($taskSignal.process_id -eq $taskWorker.Id) -or ($taskSignalProcess -and $taskSignalProcess.ParentProcessId -eq $taskWorker.Id)
            if ($taskSignal.signal -eq 'recent_heartbeat' -and $taskOwnedSignal) { $taskWorkerReady=$true; break }
            Start-Sleep -Milliseconds 300
        }
        if (!$taskWorkerReady) { throw 'Worker heartbeat did not become ready.' }
    }
    Write-Output "Frontend: http://127.0.0.1:5173 ; API: http://127.0.0.1:$taskPort"
    if ($ApiOnly) { Write-Output 'API-only diagnostic mode: database/worker/sandbox readiness NOT verified.' }
} catch {
    & (Join-Path $PSScriptRoot 'stop.ps1') -KeepDatabase
    throw
}
