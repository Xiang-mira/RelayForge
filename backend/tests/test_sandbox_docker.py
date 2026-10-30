from pathlib import Path
from uuid import uuid4

import pytest

from relayforge.workspaces.manager import Workspaces
from relayforge.workspaces.sandbox import DockerSandbox

pytestmark = pytest.mark.docker


def test_real_container_tests_fail_then_pass_under_actual_sandbox_limits(tmp_path):
    workspaces = Workspaces(tmp_path / "workspaces")
    branch = str(uuid4())
    sample = Path(__file__).resolve().parents[2] / "demo" / "sample_repo"
    workspaces.create(branch, sample)
    sandbox = DockerSandbox(workspaces)
    before = sandbox.run_tests(branch)
    assert before.exit_code == 1 and not before.passed
    assert "FAILED" in before.output and "Ran 3 tests" in before.output
    workspaces.write(branch, "calculator.py", "def add(a, b):\n    return a + b\n")
    security = """import os, socket, unittest
from pathlib import Path

class SandboxTests(unittest.TestCase):
    def test_process_and_filesystem_isolation(self):
        self.assertNotEqual(os.getuid(), 0)
        status = Path('/proc/self/status').read_text()
        caps = next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:'))
        self.assertEqual(int(caps, 16), 0)
        self.assertIn('NoNewPrivs:\\t1', status)
        root = next(line for line in Path('/proc/mounts').read_text().splitlines() if line.split()[1] == '/')
        self.assertIn('ro', root.split()[3].split(','))
        Path('/tmp/relayforge-probe').write_text('writable tmpfs')
        self.assertFalse(Path('/var/run/docker.sock').exists())

    def test_resource_limits_and_no_model_credentials(self):
        cgroup = Path('/sys/fs/cgroup')
        if (cgroup / 'memory.max').exists():
            memory = (cgroup / 'memory.max').read_text().strip()
            pids = (cgroup / 'pids.max').read_text().strip()
            quota, period = (cgroup / 'cpu.max').read_text().split()
        else:
            memory = (cgroup / 'memory/memory.limit_in_bytes').read_text().strip()
            pids = (cgroup / 'pids/pids.max').read_text().strip()
            quota = (cgroup / 'cpu/cpu.cfs_quota_us').read_text().strip()
            period = (cgroup / 'cpu/cpu.cfs_period_us').read_text().strip()
        self.assertEqual(memory, '268435456')
        self.assertEqual(pids, '64')
        self.assertLessEqual(int(quota) / int(period), 1)
        self.assertNotIn('OPENAI_API_KEY', os.environ)
        self.assertNotIn('MODEL_API_KEY', os.environ)

    def test_network_disabled(self):
        sock = socket.socket()
        sock.settimeout(0.5)
        try:
            with self.assertRaises(OSError):
                sock.connect(('1.1.1.1', 443))
        finally:
            sock.close()
"""
    workspaces.write(branch, "test_sandbox.py", security)
    after = sandbox.run_tests(branch)
    assert after.exit_code == 0, after.output
    assert after.passed and "Ran 6 tests" in after.output
