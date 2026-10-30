#requires -Version 7.2
param(
    [string[]]$Templates,
    [string[]]$Faults,
    [ValidateRange(1,5)][int]$Repeats = 1,
    [string]$SuiteId
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if ($SuiteId -and ($Templates -or $Faults)) { throw 'Resume an existing suite without changing its task/fault configuration.' }
$taskArguments = @('run','--locked','python','-m','relayforge.evaluation.runner')
if ($SuiteId) { $taskArguments += @('--suite-id',$SuiteId) }
else {
    $taskArguments += @('--repeats',"$Repeats")
    if ($Templates) { $taskArguments += @('--templates') + $Templates }
    if ($Faults) { $taskArguments += @('--faults') + $Faults }
}
& (Join-Path $PSScriptRoot 'stop.ps1') -KeepDatabase
try {
    & (Join-Path $PSScriptRoot 'start.ps1') -NoWorker
    Push-Location -LiteralPath (Join-Path $taskRoot 'backend')
    try {
        & (Join-Path $taskRoot '.tools\Scripts\uv.exe') @taskArguments
        if ($LASTEXITCODE -ne 0) { throw 'Evaluation did not pass execution checks. Persisted cases and raw reports are retained.' }
    } finally { Pop-Location }
} finally {
    & (Join-Path $PSScriptRoot 'stop.ps1') -KeepDatabase
    & (Join-Path $PSScriptRoot 'start.ps1')
}
