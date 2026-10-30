#requires -Version 7.2
$taskDockerCommand=Get-Command docker -ErrorAction SilentlyContinue
if($taskDockerCommand){$taskDockerExecutable=$taskDockerCommand.Source}
else{
    $taskDockerExecutable=Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'
    if(!(Test-Path -LiteralPath $taskDockerExecutable)){throw 'Docker CLI missing. Install/verify Docker Desktop first.'}
}
$env:PATH=(Split-Path $taskDockerExecutable -Parent)+[System.IO.Path]::PathSeparator+$env:PATH
if(!$env:DOCKER_CONTEXT){$env:DOCKER_CONTEXT='desktop-linux'}
# Scoped to this PowerShell process and its children; no registry/global context changes.
