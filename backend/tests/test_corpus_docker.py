import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from relayforge.providers.mock_coding import MockCodingProvider
from relayforge.providers.templates import TEMPLATES
from relayforge.workspaces.manager import Workspaces
from relayforge.workspaces.sandbox import DockerSandbox

pytestmark = pytest.mark.docker


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_each_distinct_template_fails_before_repair_and_passes_trusted_tests(tmp_path, template):
    root = Path(__file__).resolve().parents[2]
    source = (
        root / "demo" / ("sample_repo" if template == "fix_addition" else "templates/" + template)
    )
    workspaces = Workspaces(tmp_path / "workspaces")
    branch_id = str(uuid4())
    workspaces.create(branch_id, source)
    original = workspaces.read(branch_id, "calculator.py")
    trusted_hash = hashlib.sha256(
        workspaces.file(branch_id, "test_calculator.py").read_bytes()
    ).hexdigest()
    sandbox = DockerSandbox(workspaces)
    broken = sandbox.run_tests(branch_id)
    assert broken.exit_code != 0 and not broken.passed and not broken.timed_out
    workspaces.write(branch_id, "calculator.py", MockCodingProvider.patch(template, original))
    repaired = sandbox.run_tests(branch_id)
    assert repaired.exit_code == 0 and repaired.passed and not repaired.timed_out
    assert (
        hashlib.sha256(workspaces.file(branch_id, "test_calculator.py").read_bytes()).hexdigest()
        == trusted_hash
    )
