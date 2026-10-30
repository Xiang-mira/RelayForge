"""Launch one real OpenCode task. This driver records events; it chooses no tools or patches."""

import json
import argparse
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MODEL = "openai/gpt-6.1-sol"
PROMPT = """Repair a new managed Python parameter parser using RelayForge MCP exclusively.
Create exactly one task with template=mcp_header_parser and a descriptive title.
Read its repository, calculator.py and test_calculator.py using MCP. Reproduce the
current bug with the registered pytest Docker profile before editing. Analyze the
actual source, contract and failed test output yourself; choose and generate your
own correction. If tests still fail, read the real feedback and choose your own next
action. No external script will supply a patch. Never edit trusted tests or change
the registered profile. Inspect the actual final diff, then request finish_task;
only its successful result is acceptance. On UNKNOWN, pending approval, denial or
budget exhaustion, inspect status and stop rather than changing identity to retry.
Prepare_action persists action IDs; use a fresh request_key for each NEW patch/test/
finish, and the same key/parameters only when recovering that exact operation.
Use only relayforge_* tools; no host file, shell, web, skill, or delegation tools.
Do not include context_id in your final response. Report real failures honestly.
"""


def require_oauth(executable, environment, cwd):
    """Fail before inference if the official credential route/config is no longer OAuth-only."""
    data_home = Path(environment.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    credentials = json.loads((data_home / "opencode/auth.json").read_text(encoding="utf-8"))
    if credentials.get("openai", {}).get("type") != "oauth":
        raise RuntimeError("ChatGPT OAuth required; API-key inference is not authorized")
    effective = json.loads(subprocess.check_output(
        [str(executable), "debug", "config", "--pure"], cwd=cwd, env=environment,
        text=True, encoding="utf-8", timeout=30,
    ))
    if effective.get("enabled_providers") != ["openai"]:
        raise RuntimeError("Only the authorized OpenAI OAuth provider may be enabled")
    if effective.get("provider", {}).get("openai"):
        raise RuntimeError("Custom credential or endpoint overrides require separate review")
    expected = json.loads((ROOT / "integrations/opencode/opencode.json").read_text(encoding="utf-8"))
    if effective.get("mcp") != expected["mcp"] or effective.get("permission") != expected["permission"]:
        raise RuntimeError("Only the project MCP bridge and restricted permissions are authorized")
    agent = json.loads(subprocess.check_output(
        [str(executable), "debug", "agent", "relayforge", "--pure"], cwd=cwd, env=environment,
        text=True, encoding="utf-8", timeout=30,
    ))
    if any(agent.get("tools", {}).values()) or agent.get("steps") != 28:
        raise RuntimeError("Native tools must be disabled and iteration budget must remain bounded")
    return {"credential_type": "oauth", "provider": "openai", "native_tools_enabled": False,
            "max_agent_steps": 28, "api_key_fallback": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true", help="Verify authorization/config without any inference")
    parser.add_argument("--template", choices=["mcp_header_parser", "mcp_csv_records", "mcp_command_words", "mcp_query_codec"], default="mcp_header_parser")
    parser.add_argument("--record-evidence", action="store_true", help="Import attributed client records after the run")
    options = parser.parse_args()
    stamp = str(uuid4())
    directory = ROOT / ".local/reports" / ("opencode-model-" + stamp)
    environment = dict(os.environ)
    # Deliberately no API-key inference or fallback to another provider.
    for key in list(environment):
        if key.upper().endswith("API_KEY") or key.startswith("OPENCODE_CONFIG"):
            environment.pop(key)
    environment.update(
        OPENCODE_CONFIG=str(ROOT / "integrations/opencode/opencode.json"),
        NO_PROXY="localhost,127.0.0.1,::1", PYTHONIOENCODING="utf-8",
    )
    # Opt-in process proxy comes from the invoking shell, never system configuration.
    executable = ROOT / ".tools/opencode/node_modules/opencode-windows-x64/bin/opencode.exe"
    guard = require_oauth(executable, environment, ROOT / ".local/opencode-client")
    if options.check_only:
        print(json.dumps(guard))
        return 0
    directory.mkdir()
    prompt = PROMPT.replace("template=mcp_header_parser", "template=" + options.template)
    command = [str(executable), "run", "--pure", "--format", "json", "--agent", "relayforge",
               "--model", MODEL, "--title", "RelayForge live " + options.template + " " + stamp[:8], prompt]
    secrets = set()

    def find_secrets(value, depth=0):
        if depth > 12:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "context_id" and isinstance(child, str):
                    secrets.add(child)
                else:
                    find_secrets(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                find_secrets(child, depth + 1)
        elif isinstance(value, str):
            try:
                find_secrets(json.loads(value), depth + 1)
            except (ValueError, TypeError):
                for match in re.finditer(r'context_id[\\"\s:]+([0-9a-f]{64})', value):
                    secrets.add(match.group(1))

    events = queue.Queue()
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=ROOT / ".local/opencode-client", env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def drain(stream, kind):
        for line in stream:
            events.put((kind, line))
        events.put((kind, None))

    for stream, kind in [(process.stdout, "events"), (process.stderr, "stderr")]:
        threading.Thread(target=drain, args=(stream, kind), daemon=True).start()
    print(json.dumps({"report_directory": str(directory), "pid": process.pid, "model": MODEL}), flush=True)
    with (directory / "events.jsonl").open("x", encoding="utf-8") as transcript, \
            (directory / "stderr.log").open("x", encoding="utf-8") as errors:
        ended = set()
        timed_out = False
        while len(ended) < 2:
            if time.monotonic() - started > 600 and process.poll() is None:
                timed_out = True
                process.terminate()
            try:
                kind, line = events.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                ended.add(kind)
                continue
            try:
                event = json.loads(line)
                find_secrets(event)
            except ValueError:
                event = None
            for token in secrets:
                line = line.replace(token, "<redacted-context>")
            (transcript if kind == "events" else errors).write(line)
            (transcript if kind == "events" else errors).flush()
            if event:
                part = event.get("part", {})
                print(json.dumps({"type": event.get("type"), "session": event.get("sessionID"),
                                  "tool": part.get("tool"), "state": part.get("state", {}).get("status")}), flush=True)
    code = process.wait(timeout=10)
    summary = {"model": MODEL, "exit_code": code, "timed_out": timed_out,
               "elapsed_seconds": round(time.monotonic() - started, 2),
               "completed_at": datetime.now(timezone.utc).isoformat(),
               "driver_role": "One initial prompt, bounded process, redacted observation only; no patch/tool selection",
               "api_key_created": False, "credential_route": "OpenCode official OpenAI ChatGPT OAuth"}
    summary["preflight"] = guard
    (directory / "launch.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    if options.record_evidence:
        from relayforge.agent_evidence import record
        print(json.dumps(record(directory)), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
