#requires -Version 7.2
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $taskRoot 'backend/.venv/Scripts/python.exe') -m relayforge.mcp_demo
if ($LASTEXITCODE -ne 0) { throw 'Actual MCP feedback verification failed; no success claimed.' }
