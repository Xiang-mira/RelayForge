"""Deterministic feedback proof over real MCP stdio, with no model inference."""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from relayforge.config import ROOT, Settings
from relayforge.journal.state import idempotency_key


async def feedback(client, *, trace=None):
    trace = trace if trace is not None else []
    discovered = await client.list_tools()
    tools = sorted(t.name for t in discovered.tools)
    assert len(tools) == 9 and "finish_task" in tools

    async def call(name, args, expected="OK"):
        result = await client.call_tool(name, args)
        payload = result.structured_content
        assert isinstance(payload, dict) and payload["status"] == expected, (name, payload)
        assert result.is_error == (expected != "OK")
        recorded = json.loads(json.dumps(payload))
        recorded.get("data", {}).pop("context_id", None)
        trace.append({"tool": name, "response": recorded})
        return payload["data"]

    bound = await call("begin_task", {"title": "MCP deterministic shipping feedback",
                                     "agent_session_ref": "scripted-feedback"})
    context = {"context_id": bound["context_id"]}

    async def action(key, operation):
        return (await call("prepare_action", {**context, "request_key": key, "operation": operation}))["action_id"]

    await call("inspect_repository", context)
    initial = await call("read_file", {**context, "path": "calculator.py"})
    assert "return 0\n" in initial["content"]
    wrong = initial["content"].replace("return 0\n", "return 0 if total > 100 else 8\n", 1)
    patch_one = await action("first-proposal", "apply_patch")
    await call("apply_patch", {**context, "action_id": patch_one, "path": "calculator.py",
                               "content": wrong, "expected_hash": initial["sha256"]})
    first_test = await action("first-test", "run_tests")
    failed = await call("run_tests", {**context, "action_id": first_test}, expected="FAILED")
    assert failed["exit_code"] != 0 and not failed["passed"]
    # The observed boundary failure selects the second proposal; no canned success response.
    assert "test_exact_threshold_is_free" in failed["output"]
    assert "exact threshold must ship free" in failed["stderr"]
    premature = await action("premature-finish", "finish_task")
    await call("finish_task", {**context, "action_id": premature}, expected="NEEDS_REVALIDATION")
    current = await call("read_file", {**context, "path": "calculator.py"})
    repaired = current["content"].replace("total > 100", "total >= 100", 1)
    assert repaired != wrong
    patch_two = await action("boundary-proposal", "apply_patch")
    assert patch_two != patch_one
    await call("apply_patch", {**context, "action_id": patch_two, "path": "calculator.py",
                               "content": repaired, "expected_hash": current["sha256"]})
    test_two = await action("boundary-test", "run_tests")
    assert test_two != first_test
    passed = await call("run_tests", {**context, "action_id": test_two})
    assert passed["exit_code"] == 0 and passed["passed"] and passed["test_count"] == 3
    diff = await call("inspect_diff", context)
    assert "+    return 0 if total >= 100 else 8" in diff["diff"]
    finish = await action("final-publication", "finish_task")
    completed = await call("finish_task", {**context, "action_id": finish})
    assert completed["result"] == "SUCCEEDED"
    status = await call("get_execution_status", context)
    assert status["state"] == "SUCCEEDED" and not status["unknown"]
    return {"tools": tools, "task_id": bound["task_id"], "run_id": bound["run_id"],
            "branch_id": status["branch_id"], "publication_action_id": finish,
            "first_test_exit_code": failed["exit_code"], "second_test_exit_code": passed["exit_code"],
            "result": completed["result"], "trace": trace}


async def main_async(args):
    identity = str(uuid4())
    log = ROOT / ".local/logs" / ("mcp-feedback-" + identity + ".stderr.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
           "RELAYFORGE_API_URL": args.api_url}
    params = StdioServerParameters(command=sys.executable, args=["-m", "relayforge.mcp_server"],
                                   cwd=ROOT / "backend", env=env)
    with log.open("x", encoding="utf-8") as errors:
        async with Client(stdio_client(params, errlog=errors), read_timeout_seconds=60) as client:
            result = await feedback(client)
            result["mcp_protocol_version"] = client.protocol_version
    # Independent observation after completion, never a recovery oracle for the runtime.
    truth_path = Settings().mock_external_path
    with sqlite3.connect(truth_path.as_uri() + "?mode=ro", uri=True) as connection:
        key = idempotency_key(result["branch_id"], result["publication_action_id"])
        requests = connection.execute("SELECT count(*) FROM requests WHERE action_key=?", (key,)).fetchone()[0]
        operations = connection.execute("SELECT count(*) FROM operations WHERE action_key=?", (key,)).fetchone()[0]
    assert requests == operations == 1
    result.update(independent_external_requests=requests, independent_external_operations=operations,
                  stderr_log=str(log), model_inference=False)
    path = args.report or ROOT / ".local/reports" / ("mcp-feedback-" + identity + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"result": result["result"], "run_id": result["run_id"],
                      "first_test_exit": result["first_test_exit_code"],
                      "second_test_exit": result["second_test_exit_code"],
                      "external_requests": requests, "external_operations": operations,
                      "report": str(path)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--report", type=Path)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
