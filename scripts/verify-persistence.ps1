#requires -Version 7.2
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $taskRoot
try{
    . (Join-Path $PSScriptRoot 'docker-env.ps1')
    $taskPython=Join-Path $taskRoot 'backend\.venv\Scripts\python.exe'
    & $taskPython (Join-Path $PSScriptRoot 'persistence_probe.py') prepare
    if($LASTEXITCODE -ne 0){throw 'Persistence probe setup failed'}
    & $taskDockerExecutable compose restart postgres
    if($LASTEXITCODE -ne 0){throw 'PostgreSQL restart failed'}
    & $taskDockerExecutable compose up -d --wait postgres
    if($LASTEXITCODE -ne 0){throw 'PostgreSQL readiness after restart failed'}
    & $taskPython (Join-Path $PSScriptRoot 'persistence_probe.py') verify
    if($LASTEXITCODE -ne 0){throw 'Database persistence verification failed'}
}finally{Pop-Location}
