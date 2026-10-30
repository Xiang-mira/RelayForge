#requires -Version 7.2
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$taskPython=Join-Path $taskRoot 'backend\.venv\Scripts\python.exe'
& $taskPython -m alembic -c (Join-Path $taskRoot 'alembic.ini') upgrade head
if($LASTEXITCODE -ne 0){throw 'Database migration failed; no tables or volumes were reset.'}
