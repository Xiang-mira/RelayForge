import importlib.util
import json
from pathlib import Path

import pytest

from relayforge.config import ROOT

spec = importlib.util.spec_from_file_location("opencode_verifier", ROOT / "scripts/verify-opencode-model.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_paid_credential_rejected_before_any_client_execution(tmp_path, monkeypatch):
    directory = tmp_path / "opencode"
    directory.mkdir()
    (directory / "auth.json").write_text(json.dumps({"openai": {"type": "api", "key": "test-placeholder"}}))
    def forbidden(*args, **kwargs):
        pytest.fail("API-key route must be rejected before any subprocess is run")
    monkeypatch.setattr(verifier.subprocess, "check_output", forbidden)
    with pytest.raises(RuntimeError, match="OAuth required"):
        verifier.require_oauth(Path("unused"), {"XDG_DATA_HOME": str(tmp_path)}, tmp_path)


def test_oauth_preflight_rejects_native_tools_and_endpoint_overrides(tmp_path, monkeypatch):
    directory = tmp_path / "opencode"
    directory.mkdir()
    (directory / "auth.json").write_text(json.dumps({"openai": {"type": "oauth"}}))
    environment = {"XDG_DATA_HOME": str(tmp_path)}
    example = json.loads((ROOT / "integrations/opencode/opencode.json").read_text(encoding="utf-8"))
    config = {"enabled_providers": ["openai"], "mcp": example["mcp"], "permission": example["permission"]}
    agent = {"steps": 28, "tools": {"bash": False}}
    monkeypatch.setattr(verifier.subprocess, "check_output", lambda command, **kw:
                        json.dumps(agent if "agent" in command else config))
    assert verifier.require_oauth(Path("unused"), environment, tmp_path)["api_key_fallback"] is False
    agent["tools"]["bash"] = True
    with pytest.raises(RuntimeError, match="Native tools"):
        verifier.require_oauth(Path("unused"), environment, tmp_path)
    config["provider"] = {"openai": {"options": {"apiKey": "test-placeholder"}}}
    with pytest.raises(RuntimeError, match="overrides"):
        verifier.require_oauth(Path("unused"), environment, tmp_path)
    config["provider"] = {"openai": {"models": {"custom": {"options": {"apiKey": "test-placeholder"}}}}}
    with pytest.raises(RuntimeError, match="overrides"):
        verifier.require_oauth(Path("unused"), environment, tmp_path)
    config.pop("provider")
    config["mcp"] = {}
    with pytest.raises(RuntimeError, match="project MCP"):
        verifier.require_oauth(Path("unused"), environment, tmp_path)
