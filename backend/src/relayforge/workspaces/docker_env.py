import os
import shutil
from pathlib import Path


def docker_cli() -> str:
    resolved = shutil.which("docker")
    if resolved:
        return resolved
    if os.name == "nt":
        candidate = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "DockerDesktop"
            / "resources"
            / "bin"
            / "docker.exe"
        )
        if candidate.is_file():
            return str(candidate)
    return "docker"  # Missing executable is reported explicitly by the sandbox.


def docker_environment(cli: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = str(Path(cli).parent) + os.pathsep + environment.get("PATH", "")
    if os.name == "nt":
        environment.setdefault("DOCKER_CONTEXT", "desktop-linux")
    return environment
