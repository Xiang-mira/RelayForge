import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from sqlalchemy import select, text

from relayforge.config import ROOT
from relayforge.mcp_demo import feedback
from relayforge.models import Effect

pytestmark = [pytest.mark.postgres, pytest.mark.docker]


class ExecutionServer:
    def __init__(self, pg, directory):
        directory.mkdir(parents=True)
        self.directory = directory
        with pg[0].connect() as connection:
            schema = connection.scalar(text("SELECT current_schema()"))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "DATABASE_URL": pg[0].url.update_query_dict(
            {"options": "-csearch_path=" + schema}).render_as_string(hide_password=False),
            "WORKSPACE_ROOT": str(directory / "workspaces"),
            "MOCK_EXTERNAL_PATH": str(directory / "external.sqlite3"),
            "MCP_TOKEN_PATH": str(directory / "bridge.token"), "PYTHONIOENCODING": "utf-8"}
        self.process = None
        self.logs = []

    def start(self, *, crash=False):
        log = (self.directory / f"api-{len(self.logs)}.log").open("x", encoding="utf-8")
        self.logs.append(log)
        command = [sys.executable, str(ROOT / "backend/tests/mcp_api_process.py"), "--port", str(self.port)]
        if crash:
            command.append("--crash-after-commit")
        self.process = subprocess.Popen(command, env=self.env, cwd=ROOT / "backend", stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.fail("Isolated execution API exited during startup; inspect test-owned log")
            try:
                if httpx.get(self.url + "/health", timeout=1, trust_env=False).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        pytest.fail("Isolated execution API did not become ready")

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        for log in self.logs:
            log.close()

    def stdio(self):
        # The MCP subprocess receives no database URL and cannot select a schema/workspace.
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
        }}
        environment.update(RELAYFORGE_API_URL=self.url,
                           RELAYFORGE_MCP_TOKEN_FILE=str(self.directory / "bridge.token"),
                           PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        return StdioServerParameters(command=sys.executable, args=["-m", "relayforge.mcp_server"],
                                     cwd=ROOT / "backend", env=environment)


@contextmanager
def execution(pg, directory, *, crash=False):
    server = ExecutionServer(pg, directory)
    try:
        server.start(crash=crash)
        yield server
    finally:
        server.close()


def test_real_stdio_protocol_feedback_and_context_rejection(pg, tmp_path):
    with execution(pg, tmp_path / "api") as server:
        stderr = tmp_path / "mcp.stderr.log"

        async def scenario():
            with stderr.open("x", encoding="utf-8") as errors:
                async with Client(stdio_client(server.stdio(), errlog=errors), read_timeout_seconds=60) as client:
                    invalid = await client.call_tool("read_file", {"context_id": "0" * 64, "path": "../.env"})
                    assert invalid.is_error and invalid.structured_content["status"] == "INVALID_CONTEXT"
                    negative = await client.call_tool("begin_task", {"title": "Protocol path boundary"})
                    token = negative.structured_content["data"]["context_id"]
                    denied = await client.call_tool("read_file", {"context_id": token, "path": "../.env"})
                    assert denied.is_error and denied.structured_content["status"] == "DENIED"
                    result = await feedback(client)
                    assert result["first_test_exit_code"] == 1 and result["second_test_exit_code"] == 0
                    assert client.protocol_version
                    return result

        result = asyncio.run(scenario())
        from relayforge.config import Settings
        from relayforge.runtime.evidence import RuntimeEvidence
        view = RuntimeEvidence(pg[1], Settings(workspace_root=server.directory / "workspaces",
                                               mock_external_path=server.directory / "external.sqlite3")).view(result["run_id"])
        assert view["iteration"]["failure_after_patch_then_revision"] is True
        assert view["agent"] is None  # Script feedback must never become model evidence.
        logs = stderr.read_text(encoding="utf-8")
        assert "Traceback" not in logs and "Failed to parse" not in logs
        assert (server.directory / "bridge.token").read_text() not in logs
        with sqlite3.connect((server.directory / "external.sqlite3").as_uri() + "?mode=ro", uri=True) as truth:
            assert truth.execute("SELECT count(*) FROM requests").fetchone()[0] == 1
            assert truth.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        with pg[1]() as session:
            writes = session.scalars(select(Effect).where(
                Effect.run_id == result["run_id"], Effect.tool_name == "workspace.write"
            )).all()
            assert len(writes) == 2 and writes[0].logical_action_id != writes[1].logical_action_id
        assert httpx.post(server.url + "/api/controlled/begin_task", json={"arguments": {}},
                          timeout=5, trust_env=False).status_code == 403


def test_stdio_survives_execution_process_crash_and_reconciles_original_commit(pg, tmp_path):
    with execution(pg, tmp_path / "crash-api", crash=True) as server:
        stderr = tmp_path / "crash-mcp.stderr.log"

        async def scenario():
            with stderr.open("x", encoding="utf-8") as errors:
                async with Client(stdio_client(server.stdio(), errlog=errors), read_timeout_seconds=60) as client:
                    async def call(name, arguments, status="OK"):
                        result = await client.call_tool(name, arguments)
                        assert result.structured_content["status"] == status, result.structured_content
                        assert result.is_error == (status != "OK")
                        return result.structured_content["data"]

                    bound = await call("begin_task", {})
                    context = {"context_id": bound["context_id"]}

                    async def intent(key, operation):
                        return (await call("prepare_action", {**context, "request_key": key,
                                                              "operation": operation}))["action_id"]

                    source = await call("read_file", context)
                    patch = await intent("repair", "apply_patch")
                    await call("apply_patch", {**context, "action_id": patch, "path": "calculator.py",
                                               "expected_hash": source["sha256"],
                                               "content": source["content"].replace("return 0", "return 0 if total >= 100 else 8")})
                    test = await intent("test", "run_tests")
                    assert (await call("run_tests", {**context, "action_id": test}))["passed"]
                    finish = await intent("publish", "finish_task")
                    pid_before = server.process.pid
                    await call("finish_task", {**context, "action_id": finish}, status="UNKNOWN")
                    assert server.process.wait(timeout=10) == 86
                    with pg[1]() as session:
                        effect = session.scalar(select(Effect).where(Effect.logical_action_id == finish))
                        assert effect.status == "IN_FLIGHT" and effect.external_receipt is None
                    path = server.directory / "external.sqlite3"
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as truth:
                        assert truth.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
                    server.start()
                    assert server.process.pid != pid_before
                    rebound = await call("begin_task", context)
                    assert rebound["run_id"] == bound["run_id"]
                    assert rebound["budget"]["tests_left"] == 3
                    finished = await call("finish_task", {**context, "action_id": finish})
                    assert finished["result"] == "SUCCEEDED"
                    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as truth:
                        requests = truth.execute("SELECT count(*) FROM requests").fetchone()[0]
                        operations = truth.execute("SELECT count(*) FROM operations").fetchone()[0]
                        assert requests == operations == 1
                    with pg[1]() as session:
                        recovered = session.get(Effect, effect.effect_id)
                        assert recovered.status == "CONFIRMED" and recovered.reconciliation_method == "provider_query"
                    proof = {"run_id": bound["run_id"], "effect_id": effect.effect_id,
                             "process_before": pid_before, "process_after": server.process.pid,
                             "crash_exit": 86, "requests": requests, "operations": operations,
                             "recovery": "provider_query", "scope": "isolated test schema"}
                    destination = ROOT / ".local/reports" / f"mcp-crash-{bound['run_id']}.json"
                    with destination.open("x", encoding="utf-8") as stream:
                        json.dump(proof, stream, indent=2)

        asyncio.run(scenario())
