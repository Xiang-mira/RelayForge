import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from uuid import uuid4

from relayforge.workspaces.docker_env import docker_cli, docker_environment
from relayforge.workspaces.manager import Workspaces


@dataclass(frozen=True)
class TestResult:
    exit_code: int
    output: str
    timed_out: bool = False
    fault_injected: bool = False
    stdout: str = ""
    stderr: str = ""

    @property
    def test_count(self) -> int:
        # unittest exits zero even with no discovered tests; require a real nonzero count.
        import re

        matches = re.findall(r"Ran (\d+) tests? in", self.output)
        if matches:
            return int(matches[-1])
        # pytest's final summary; skipped-only/no-collection is never a passing test.
        summaries = re.findall(r"^=+ (.+?) =+$", self.output, re.MULTILINE)
        return sum(int(n) for n in re.findall(r"(\d+) (?:passed|failed|error\b|errors\b)", summaries[-1])) if summaries else 0

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.test_count > 0


class DockerSandbox:
    def __init__(self, workspaces: Workspaces, image="relayforge-sandbox:local"):
        self.workspaces = workspaces
        self.image = image
        self.cli = docker_cli()

    def test_configuration(self, *, branch_id: str | None = None, resolve_image: bool = False) -> dict:
        image = self.image
        command = ["python", "-I", "-m", "unittest", "discover", "-v"]
        if branch_id:
            profile = self.workspaces.file(branch_id, ".relayforge-test-profile")
            if profile.exists():
                if profile.read_text(encoding="utf-8").strip() != "pytest-header-v1":
                    raise ValueError("Unregistered test profile")
                image = "relayforge-sandbox:pytest-header-v1"
                command = ["python", "-I", "-m", "pytest", "-v", "--color=no", "-p", "no:cacheprovider", "--import-mode=importlib"]
        configuration = {
            "contract": 1,
            "image": image,
            "command": command,
            "network": "none",
            "memory": "256m",
            "cpus": "1",
            "pids": 64,
            "user": "65532:65532",
            "timeout_seconds": 30,
            "workspace_readonly": True,
        }
        if resolve_image:
            try:
                inspected = subprocess.run(
                    [self.cli, "image", "inspect", "--format", "{{.Id}}", image],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=docker_environment(self.cli),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Sandbox image identity unavailable") from None
            import re

            image_id = inspected.stdout.strip()
            if inspected.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise ValueError("Sandbox image identity unavailable")
            configuration["image_id"] = image_id
        return configuration

    def command(
        self, branch_id: str, container_name: str, snapshot_source: Path | None = None
    ) -> list[str]:
        workspace = snapshot_source or self.workspaces.branch_path(branch_id)
        if not workspace.is_dir():
            raise ValueError("Managed workspace missing")
        if "," in str(workspace):
            raise ValueError("Mount path contains unsupported delimiter")
        test_mount = []
        acceptance = workspace / "test_calculator.py"
        if acceptance.is_file():
            test_mount = [
                "--mount",
                f"type=bind,source={acceptance},target=/workspace/test_calculator.py,readonly",
            ]
        configuration = self.test_configuration(branch_id=branch_id)
        return [
            self.cli,
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            "relayforge.managed=true",
            "--label",
            f"relayforge.branch={branch_id}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--cpus",
            "1",
            "--memory",
            "256m",
            "--user",
            "65532:65532",
            "--mount",
            f"type=bind,source={workspace},target=/workspace"
            + (",readonly" if snapshot_source else ""),
            *test_mount,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            configuration["image"],
            *configuration["command"],
        ]

    def run_tests(
        self,
        branch_id: str,
        timeout: int = 30,
        terminate: bool = False,
        snapshot_source: Path | None = None,
        image_id: str | None = None,
    ) -> TestResult:
        name = "relayforge-test-" + uuid4().hex
        command = self.command(branch_id, name, snapshot_source)
        configuration = self.test_configuration(branch_id=branch_id)
        image_position = command.index(configuration["image"])
        if image_id:
            command[image_position] = image_id
        if terminate:
            # Fixed fault gate inside Docker; never arbitrary host shell/model commands.
            command[image_position + 1:] = ["python", "-I", "-c", "import time; time.sleep(30)"]
        chunks = deque(maxlen=8)
        error_chunks = deque(maxlen=8)
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=docker_environment(self.cli),
            )
        except FileNotFoundError:
            return TestResult(127, "Docker CLI unavailable; no host execution fallback")

        def drain(stream, destination):
            with stream:
                while chunk := stream.read(4096):
                    destination.append(chunk)

        reader = Thread(target=drain, args=(process.stdout, chunks), daemon=True)
        error_reader = Thread(target=drain, args=(process.stderr, error_chunks), daemon=True)
        reader.start()
        error_reader.start()
        injected = False
        if terminate:
            for _ in range(30):
                if process.poll() is not None:
                    break
                inspected = subprocess.run(
                    [self.cli, "inspect", "--format", "{{.State.Running}} {{.Id}}", name],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=3,
                    env=docker_environment(self.cli),
                )
                fields = inspected.stdout.strip().split()
                if inspected.returncode == 0 and len(fields) == 2 and fields[0] == "true":
                    killed = subprocess.run(
                        [self.cli, "kill", fields[1]],
                        capture_output=True,
                        check=False,
                        timeout=5,
                        env=docker_environment(self.cli),
                    )
                    injected = killed.returncode == 0
                    break
                time.sleep(0.1)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Clean up exactly the uniquely named container owned by this call.
            try:
                subprocess.run(
                    [self.cli, "rm", "-f", name],
                    check=False,
                    capture_output=True,
                    timeout=10,
                    shell=False,
                    env=docker_environment(self.cli),
                )
            finally:
                process.kill()
                process.wait(timeout=5)
                reader.join(timeout=2)
                error_reader.join(timeout=2)
            return TestResult(124, "Sandbox exceeded execution deadline", timed_out=True,
                              stdout=b"".join(chunks).decode("utf-8", errors="replace"),
                              stderr=b"".join(error_chunks).decode("utf-8", errors="replace"))
        reader.join(timeout=2)
        error_reader.join(timeout=2)
        stdout = b"".join(chunks).decode("utf-8", errors="replace")
        stderr = b"".join(error_chunks).decode("utf-8", errors="replace")
        return TestResult(
            code, stdout + stderr, fault_injected=injected, stdout=stdout, stderr=stderr,
        )
