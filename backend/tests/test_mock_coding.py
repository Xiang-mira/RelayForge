from pathlib import Path

import pytest

from relayforge.providers.mock_coding import MockCodingProvider
from relayforge.providers.templates import TEMPLATES


def test_mock_requires_the_actual_sample_bug():
    source = "def add(a, b):\n    return a - b\n"
    assert "return a + b" in MockCodingProvider.patch("fix_addition", source)
    with pytest.raises(ValueError):
        MockCodingProvider.patch("fix_addition", "return a + b")
    with pytest.raises(ValueError):
        MockCodingProvider.plan("nonexistent", source)


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_templates_refuse_already_repaired_sources(template):
    demo = Path(__file__).resolve().parents[2] / "demo"
    source = (
        demo
        / ("sample_repo" if template == "fix_addition" else "templates/" + template)
        / "calculator.py"
    )
    original = source.read_text(encoding="utf-8")
    fixed = MockCodingProvider.patch(template, original)
    assert fixed != original
    with pytest.raises(ValueError):
        MockCodingProvider.patch(template, fixed)
