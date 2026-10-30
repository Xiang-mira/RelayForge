"""Official MCP stdio transport. All business operations go through the loopback API."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from relayforge.config import ROOT
from relayforge.runtime.bridge_auth import bridge_token


class Bridge:
    def __init__(self):
        self.url = os.environ.get("RELAYFORGE_API_URL", "http://127.0.0.1:8000").rstrip("/")
        parsed = urlsplit(self.url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Only the local RelayForge API is supported")
        self.token_path = Path(os.environ.get("RELAYFORGE_MCP_TOKEN_FILE", ROOT / ".local/mcp-api.token"))

    def call(self, operation, arguments):
        fallback = {"status": "UNKNOWN", "message": "Local runtime unavailable; inspect status before retrying",
                    "data": {}, "task_id": None, "run_id": None,
                    "action_id": arguments.get("action_id"), "evidence": None, "retryable": False}
        try:
            token = bridge_token(self.token_path)
            with httpx.Client(timeout=50, trust_env=False, follow_redirects=False) as client:
                response = client.post(self.url + "/api/controlled/" + operation,
                                       json={"arguments": arguments}, headers={"X-RelayForge-MCP": token})
            if response.status_code == 403:
                payload = {**fallback, "status": "DENIED", "message": "Local bridge authorization rejected"}
            elif response.status_code != 200:
                payload = fallback
            else:
                payload = response.json()
        except (httpx.HTTPError, OSError, ValueError):
            payload = fallback
        return CallToolResult(
            content=[TextContent(text=json.dumps(payload, ensure_ascii=False))],
            structuredContent=payload, isError=payload["status"] != "OK",
        )


def build_server():
    bridge = Bridge()
    server = MCPServer("RelayForge", version="0.2.0", log_level="WARNING",
                       instructions="Bounded local runtime tools. Use prepare_action before mutations/tests/finish. "
                       "A failed test is feedback, not task completion. Never invent success or retry UNKNOWN blindly.")

    @server.tool()
    def begin_task(title: str = "MCP shipping feedback", context_id: str | None = None,
                   agent_session_ref: str = "stdio-client", template: str = "mcp_feedback") -> CallToolResult:
        """Create mcp_feedback, mcp_header_parser, mcp_csv_records, mcp_command_words or mcp_query_codec;
        or rebind an existing context without resetting budgets. Inspect source/tests for the task contract."""
        return bridge.call("begin_task", {"title": title, "context_id": context_id,
                                          "agent_session_ref": agent_session_ref, "template": template})

    @server.tool()
    def prepare_action(context_id: str, request_key: str, operation: str) -> CallToolResult:
        """Persist an intent ID for apply_patch, run_tests or finish_task. Reuse request_key to recover the ID."""
        return bridge.call("prepare_action", {"context_id": context_id, "request_key": request_key,
                                              "operation": operation})

    @server.tool()
    def inspect_repository(context_id: str) -> CallToolResult:
        """List bounded real source/test files in the authorized workspace, with content hashes."""
        return bridge.call("inspect_repository", {"context_id": context_id})

    @server.tool()
    def read_file(context_id: str, path: str = "calculator.py") -> CallToolResult:
        """Read a permitted text file through the runtime. Returns current content and raw-byte SHA256."""
        return bridge.call("read_file", {"context_id": context_id, "path": path})

    @server.tool()
    def apply_patch(context_id: str, action_id: str, path: str, content: str,
                    expected_hash: str) -> CallToolResult:
        """Propose a complete replacement for calculator.py, conditional on its inspected SHA256."""
        return bridge.call("apply_patch", {"context_id": context_id, "action_id": action_id,
                                           "path": path, "content": content, "expected_hash": expected_hash})

    @server.tool()
    def run_tests(context_id: str, action_id: str, configuration: str = "unittest") -> CallToolResult:
        """Run the task's registered unittest/pytest Docker profile; no shell commands. Failed tests return feedback."""
        return bridge.call("run_tests", {"context_id": context_id, "action_id": action_id,
                                         "configuration": configuration})

    @server.tool()
    def inspect_diff(context_id: str) -> CallToolResult:
        """Read the actual diff against the initial snapshot, with current content fingerprint."""
        return bridge.call("inspect_diff", {"context_id": context_id})

    @server.tool()
    def get_execution_status(context_id: str) -> CallToolResult:
        """Query task, effects, UNKNOWN, pending approvals, latest test and persistent budgets."""
        return bridge.call("get_execution_status", {"context_id": context_id})

    @server.tool()
    def finish_task(context_id: str, action_id: str) -> CallToolResult:
        """Request existing publication and final acceptance gates; cannot assert success or grant approval."""
        return bridge.call("finish_task", {"context_id": context_id, "action_id": action_id})

    return server


def main():
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
