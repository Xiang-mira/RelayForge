#requires -Version 7.2
param([switch]$KeepDatabase)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPidFile = Join-Path $taskRoot '.local\processes.json'
$taskUnresolved = @()
if (Test-Path -LiteralPath $taskPidFile) {
    $taskOwned = @(Get-Content -LiteralPath $taskPidFile -Raw | ConvertFrom-Json)
    foreach ($taskEntry in $taskOwned) {
        $taskProcess = Get-Process -Id $taskEntry.Id -ErrorAction SilentlyContinue
        if (!$taskProcess) { continue }
        $taskActual = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskEntry.Id)"
        $taskSavedStart = ([datetime]$taskEntry.Start).ToUniversalTime()
        if (($taskProcess.StartTime.ToUniversalTime().Ticks -ne $taskSavedStart.Ticks) -or ($taskActual.ExecutablePath -ne $taskEntry.Executable)) {
            Write-Warning "Ownership mismatch for PID $($taskEntry.Id); preserved."
            $taskUnresolved += $taskEntry
            continue
        }
        Stop-Process -Id $taskEntry.Id -ErrorAction Stop
    }
    if ($taskUnresolved.Count -gt 0) {
        $taskUnresolved | ConvertTo-Json -AsArray | Set-Content -LiteralPath $taskPidFile
        throw 'Some process identities could not be verified; ledger retained.'
    }
    Remove-Item -LiteralPath $taskPidFile
}
if (!$KeepDatabase) {
    . (Join-Path $PSScriptRoot 'docker-env.ps1')
    Push-Location -LiteralPath $taskRoot
    try { & $taskDockerExecutable compose stop postgres; if ($LASTEXITCODE -ne 0) { throw 'Database stop failed.' } } finally { Pop-Location }
}
Write-Output 'Owned services stopped. Database volume and runtime data retained.'
