"""Import an actual OpenCode transcript after a bounded run, without model inference."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import httpx

from relayforge.config import ROOT, Settings
from relayforge.runtime.bridge_auth import bridge_token
from relayforge.runtime.evidence import public_text


def record(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to((ROOT / ".local/reports").resolve()):
        raise ValueError("Only project-owned evidence directories may be imported")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    session_id = next(e["sessionID"] for e in events if e.get("sessionID"))
    launch = json.loads((directory / "launch.json").read_text(encoding="utf-8"))
    if launch.get("credential_route") != "OpenCode official OpenAI ChatGPT OAuth":
        raise ValueError("No recorded OAuth provenance")
    exported = directory / "stage3-session-redacted.json"
    if exported.exists():
        source = exported.read_text(encoding="utf-8")
        document = json.loads(source)
    else:
        executable = ROOT / ".tools/opencode/node_modules/opencode-windows-x64/bin/opencode.exe"
        raw = subprocess.check_output([str(executable), "export", session_id, "--pure"],
                                      cwd=ROOT / ".local/opencode-client", text=True, encoding="utf-8", timeout=30)
        document = json.loads(raw[raw.index("{"):])
        secrets = set()

        def find(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "context_id" and isinstance(item, str):
                        secrets.add(item)
                    else:
                        find(item)
            elif isinstance(value, list):
                for item in value:
                    find(item)
            elif isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, (dict, list)):
                        find(parsed)
                except ValueError:
                    pass
        find(document)
        source = json.dumps(document, ensure_ascii=False, indent=2)
        for secret in secrets:
            source = source.replace(secret, "<redacted-context>")
        with exported.open("x", encoding="utf-8") as stream:
            stream.write(source)
        document = json.loads(source)
    assistants = [entry["info"] for entry in document["messages"] if entry["info"]["role"] == "assistant"]
    providers = {(item["providerID"], item["modelID"]) for item in assistants}
    if len(providers) != 1 or not assistants:
        raise ValueError("Mixed or missing model provenance")
    provider, model = next(iter(providers))
    if provider != "openai" or f"{provider}/{model}" != launch["model"]:
        raise ValueError("Model does not match the bounded launcher")
    calls, messages, run_ids = [], [], set()
    last_call = None
    for event in events:
        part = event.get("part", {})
        if event["type"] == "tool_use":
            state = part["state"]
            try:
                result = json.loads(state.get("output", state.get("error", "{}")))
            except ValueError:
                raise ValueError("Unstructured tool outcome cannot be attributed") from None
            if result.get("run_id"):
                run_ids.add(result["run_id"])
            if not part["tool"].startswith("relayforge_"):
                raise ValueError("Native tool invocation found; do not label this a controlled run")
            action = result.get("action_id") or result.get("data", {}).get("action_id")
            calls.append({"call_id": part["callID"], "message_id": part["messageID"], "tool": part["tool"],
                          "runtime_call_id": result.get("runtime_call_id"), "action_id": action,
                          "receipt_id": (result.get("evidence") or {}).get("external_id"), "status": result.get("status", "UNKNOWN")})
            last_call = part["callID"]
        elif event["type"] == "text":
            messages.append({"message_id": part["messageID"], "text": public_text(part["text"]),
                             "after_call_id": last_call})
    if len(run_ids) != 1:
        raise ValueError("Exactly one actual controlled run must be attributed")
    payload = {"run_id": next(iter(run_ids)), "session_id": session_id, "provider": provider, "model": model,
               "auth_route": "chatgpt_oauth", "source_hash": hashlib.sha256(source.encode()).hexdigest(),
               "calls": calls, "messages": messages}
    settings = Settings()
    with httpx.Client(trust_env=False, timeout=20) as client:
        response = client.post(f"http://127.0.0.1:{settings.api_port}/api/controlled/agent-evidence", json=payload,
                               headers={"X-RelayForge-MCP": bridge_token(settings.mcp_token_path)})
        response.raise_for_status()
    receipt = {"run_id": payload["run_id"], "session_id": session_id, **response.json()}
    target = directory / "client-evidence-import.json"
    if not target.exists():
        with target.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    print(json.dumps(record(parser.parse_args().directory)))
