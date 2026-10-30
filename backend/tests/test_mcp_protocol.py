import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from mcp.types import DEFAULT_NEGOTIATED_VERSION


def test_stdio_stdout_contains_only_actual_protocol_frames():
    process = subprocess.Popen(
        [sys.executable, "-m", "relayforge.mcp_server"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    reader = ThreadPoolExecutor(max_workers=1)

    def request(payload):
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    def reply():
        line = reader.submit(process.stdout.readline).result(timeout=15)
        assert line
        return json.loads(line)  # A stray banner/log would fail this assertion.

    try:
        request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": DEFAULT_NEGOTIATED_VERSION, "capabilities": {},
            "clientInfo": {"name": "relayforge-frame-test", "version": "1"},
        }})
        first = reply()
        assert first["id"] == 1 and first["result"]["serverInfo"]["name"] == "RelayForge"
        request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = reply()
        assert listed["id"] == 2 and len(listed["result"]["tools"]) == 9
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        for line in process.stdout.read().splitlines():
            assert isinstance(json.loads(line), dict)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.shutdown(wait=True)
        process.stdout.close()
        process.stderr.close()
