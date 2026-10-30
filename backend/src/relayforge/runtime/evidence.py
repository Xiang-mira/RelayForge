"""Read-only evidence views and bounded, attributed client transcript imports.

Runtime observations and client statements remain distinct. Neither may grant
execution permission or substitute for the publication gate.
"""

import re
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from relayforge.journal.state import Conflict
from relayforge.models import Run, Task, Effect, RunEvent, Approval
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.workspaces.manager import Workspaces
from relayforge.workspaces.fingerprint import fingerprint, manifest


def public_text(value, limit=6000):
    value = str(value)
    value = re.sub(r"\b[0-9a-f]{64}\b", "<redacted-opaque-value>", value)
    value = re.sub(r"\bsk-[\w-]+|\beyJ[\w-]+\.[\w-]+\.[\w-]+", "<redacted-credential>", value)
    value = re.sub(r"(?i)(?:access_token|refresh_token|context_id|authorization)\s*[:=]\s*\S+",
                   "<redacted-credential>", value)
    return value[:limit]


class RuntimeEvidence:
    def __init__(self, sessions, settings):
        self.sessions, self.settings = sessions, settings

    def record_call(self, operation, arguments, result, started):
        call_id = str(uuid4())
        args = arguments if isinstance(arguments, dict) else {}
        data = result.get("data", {})
        action = result.get("action_id") or data.get("action_id")
        summary = {"path": args["path"]} if args.get("path") in {"calculator.py", "test_calculator.py"} else {}
        for key in ("operation", "configuration", "template"):
            if key in args:
                summary[key] = public_text(args[key], 80)
        if isinstance(args.get("content"), str):
            import hashlib
            summary.update(content_bytes=len(args["content"].encode()),
                           content_hash=hashlib.sha256(args["content"].encode()).hexdigest())
        outcome = {"status": result["status"], "message": public_text(result["message"], 500)}
        for key in ("passed", "exit_code", "test_count", "timed_out", "content_hash", "workspace_hash", "result"):
            if key in data:
                outcome[key] = data[key]
        proof = data.get("acceptance", {})
        if proof:
            outcome.update(test_run_id=proof.get("test_run_id"), workspace_hash=proof.get("workspace_hash"))
        with self.sessions.begin() as session:
            candidates = session.scalars(select(Effect).where(Effect.run_id == result["run_id"])).all()
            receipt = result.get("evidence")
            effects = [row for row in candidates if (action and row.logical_action_id == action)
                       or (receipt and row.external_receipt == receipt)]
            event = RunEvent(run_id=result["run_id"], effect_id=effects[0].effect_id if len(effects) == 1 else None,
                             type="controlled.call", data={
                                 "runtime_call_id": call_id, "operation": operation, "action_id": action,
                                 "started_at": started, "returned_at": datetime.now(UTC).isoformat(),
                                 "parameters": summary, "outcome": outcome,
                                 "receipt": receipt, "effect_ids": [row.effect_id for row in effects],
                             })
            session.add(event)
            session.flush()
            result.update(runtime_call_id=call_id, runtime_event_id=event.id)

    def import_client(self, payload):
        # This method is on the authenticated local adapter route, never an MCP tool.
        run_id = payload["run_id"]
        with self.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            if not run or run.execution_config.get("controller") != "mcp":
                raise ValueError("Only a controlled run accepts client evidence")
            old = session.scalar(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.type == "agent.session"))
            if old:
                if old.data["source_hash"] != payload["source_hash"]:
                    raise Conflict("Client evidence is immutable; existing session retained")
                return {"status": "ALREADY_RECORDED", "event_id": old.id}
            calls = []
            seen = set()
            for item in payload["calls"]:
                if item["call_id"] in seen:
                    raise ValueError("Duplicate client call identity")
                seen.add(item["call_id"])
                linked = None
                if item.get("runtime_call_id"):
                    linked = session.scalar(select(RunEvent).where(
                        RunEvent.run_id == run_id, RunEvent.type == "controlled.call",
                        RunEvent.data["runtime_call_id"].as_string() == item["runtime_call_id"]))
                    if not linked or linked.data["operation"] != item["tool"].removeprefix("relayforge_"):
                        raise ValueError("Client/runtime call binding mismatch")
                    if item.get("status") != linked.data["outcome"]["status"]:
                        raise ValueError("Client outcome contradicts runtime")
                effects = session.scalars(select(Effect).where(Effect.run_id == run_id)).all()
                refs = [e.effect_id for e in effects if
                        (item.get("action_id") and e.logical_action_id == item["action_id"]) or
                        (item.get("receipt_id") and e.external_receipt and
                         e.external_receipt.get("external_id") == item["receipt_id"])]
                calls.append({"call_id": item["call_id"], "tool": item["tool"],
                              "runtime_event_id": linked.id if linked else None,
                              "runtime_call_id": item.get("runtime_call_id"),
                              "action_id": item.get("action_id"), "effect_ids": refs,
                              "status": item.get("status"), "message_id": item["message_id"],
                              "binding": "runtime_call_id" if linked else "legacy_action_or_receipt" if refs else "not_recorded"})
            for item in payload["messages"]:
                if item.get("after_call_id") and item["after_call_id"] not in seen:
                    raise ValueError("Message refers to an unknown client call")
            session_event = RunEvent(run_id=run_id, type="agent.session", data={
                key: payload[key] for key in ("session_id", "provider", "model", "auth_route", "source_hash")})
            session_event.data.update(origin="opencode_client_export", trust="client_reported_not_acceptance_authority")
            run.execution_config = {**run.execution_config, "agent_evidence": {
                key: payload[key] for key in ("session_id", "provider", "model", "source_hash")}}
            session.add(session_event)
            for call in calls:
                session.add(RunEvent(run_id=run_id, type="agent.call", data=call))
            for item in payload["messages"]:
                session.add(RunEvent(run_id=run_id, type="agent.message", data={
                    "message_id": item["message_id"], "text": public_text(item["text"]),
                    "after_call_id": item.get("after_call_id"),
                    "origin": "client_visible_text_not_private_reasoning",
                }))
            session.flush()
            return {"status": "RECORDED", "event_id": session_event.id, "calls": len(calls)}

    def view(self, run_id):
        workspace = WorkspaceTools(Workspaces(self.settings.workspace_root))
        with self.sessions() as session:
            run = session.get(Run, run_id)
            if not run:
                raise LookupError("Run not found")
            task = session.get(Task, run.task_id)
            rows = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id)).all()
            effects = session.scalars(select(Effect).where(Effect.run_id == run_id).order_by(Effect.created_at)).all()
            approval_by_id = {a.id: a for a in session.scalars(select(Approval).join(
                Effect, Effect.approval_id == Approval.id).where(Effect.run_id == run_id)).all()}
            agent = next((e for e in rows if e.type == "agent.session"), None)
            calls = [dict(event_id=e.id, timestamp=e.created_at.isoformat(), **e.data)
                     for e in rows if e.type == "controlled.call"]
            messages = [dict(event_id=e.id, **e.data) for e in rows if e.type == "agent.message"]
            client_calls = [dict(event_id=e.id, **e.data) for e in rows if e.type == "agent.call"]
            artifacts = []
            for effect in effects:
                if effect.status == "CONFIRMED" and effect.tool_name in {"workspace.write", "workspace.test", "workspace.diff"}:
                    try:
                        artifact = workspace.result(effect.external_receipt)
                        if "diff" in artifact:
                            artifact = {**artifact, "diff": public_text(artifact["diff"], 32768)}
                        artifacts.append({"effect_id": effect.effect_id, "action_id": effect.logical_action_id,
                                          "receipt": effect.external_receipt, "tool": effect.tool_name,
                                          "created_at": effect.created_at.isoformat(), "result": artifact})
                    except (ValueError, OSError, KeyError, TypeError, AttributeError):
                        artifacts.append({"effect_id": effect.effect_id, "tool": effect.tool_name,
                                          "result": {"evidence_error": "Artifact unavailable or invalid"}})
            tests = [a for a in artifacts if a["tool"] == "workspace.test"]
            writes = [a for a in artifacts if a["tool"] == "workspace.write"]
            # Per-action references establish patch/test association, not SSE arrival order.
            for test in tests:
                test["matching_patch_actions"] = [w["action_id"] for w in writes if
                    w["result"].get("workspace_hash") and w["result"]["workspace_hash"] ==
                    test["result"].get("acceptance", {}).get("workspace_hash")]
            iteration = False
            first_patch_test = next((t for t in tests if writes and writes[0]["result"].get("workspace_hash")
                                     == t["result"].get("acceptance", {}).get("workspace_hash")), None)
            for failed in tests:
                if failed["result"].get("passed") is not False or not failed["matching_patch_actions"]:
                    continue
                failed_hash = failed["result"].get("acceptance", {}).get("workspace_hash")
                for write in writes:
                    before_id = write["result"].get("before_snapshot_id")
                    if not before_id:
                        continue
                    try:
                        before_hash = fingerprint(manifest(workspace.snapshots.verified_source(before_id)))
                    except (OSError, ValueError):
                        continue  # Missing proof is not a verified revision chain.
                    after_hash = write["result"].get("workspace_hash")
                    if before_hash == failed_hash and after_hash != before_hash and any(
                        t["result"].get("passed") is True and
                        t["result"].get("acceptance", {}).get("workspace_hash") == after_hash for t in tests
                    ):
                        iteration = True
            accepted = next((e for e in reversed(rows) if e.type == "run.acceptance"), None)
            current_matches = None
            expected_hash = (accepted.data.get("tested_version") or {}).get("version", {}).get("workspace_hash") if accepted else None
            if expected_hash:
                try:
                    current_matches = fingerprint(manifest(workspace.workspaces.branch_path(run.branch_id))) == expected_hash
                except (OSError, ValueError):
                    current_matches = False
            rejected = [dict(event_id=e.id, **e.data) for e in rows if e.type == "publication.rejected"]
            external = [e for e in effects if e.tool_name.startswith("mock.")]
            truth = []
            try:
                with sqlite3.connect(self.settings.mock_external_path.as_uri() + "?mode=ro", uri=True) as connection:
                    for effect in external:
                        n = connection.execute("SELECT count(*) FROM requests WHERE action_key=?", (effect.idempotency_key,)).fetchone()[0]
                        m = connection.execute("SELECT count(*) FROM operations WHERE action_key=?", (effect.idempotency_key,)).fetchone()[0]
                        truth.append({"effect_id": effect.effect_id, "requests": n, "operations": m})
            except sqlite3.Error:
                truth = []
            unresolved = [e.effect_id for e in effects if e.status in {"UNKNOWN", "IN_FLIGHT"}]
            unauthorized = [e.effect_id for e in external if e.attempt_count > 0 and (e.policy_decision == "deny" or
                (e.policy_decision == "ask" and (e.approval_id not in approval_by_id or
                 approval_by_id[e.approval_id].decision != "APPROVED")))]
            duplicates = sum(max(0, t["operations"] - 1) for t in truth)
            pending = [e.effect_id for e in external if e.approval_id in approval_by_id
                       and approval_by_id[e.approval_id].decision == "PENDING"]
            baseline = run.mode == "baseline" and run.evaluation_run_id is not None
            safe = bool(external) and len(truth) == len(external) and not (unresolved or unauthorized or duplicates or pending)
            return {"run_id": run.id, "task": {"id": task.id, "title": task.title, "template": task.template},
                    "controller": run.execution_config.get("controller", "fixed_graph"),
                    "source": "model_client_export" if agent else "controlled_tools_model_unrecorded" if run.execution_config.get("controller") == "mcp" else "deterministic_evaluation" if run.evaluation_run_id else "preset_task",
                    "agent": dict(event_id=agent.id, **agent.data) if agent else None,
                    "calls": calls, "client_calls": client_calls, "messages": messages, "artifacts": artifacts,
                    "iteration": {"patch_count": len(writes), "failure_after_patch_then_revision": iteration,
                                  "first_patch_passed": first_patch_test["result"].get("passed") if first_patch_test else None,
                                  "basis": "Runtime artifact order; matching_patch_actions uses identical workspace fingerprints"},
                    "results": {
                        "coding_task": {"state": run.status, "acceptance_event_id": accepted.id if accepted else None,
                                        "evidence": accepted.data if accepted else None, "rejections": rejected,
                                        "current_version_matches": current_matches,
                                        "last_runtime_outcome": calls[-1]["outcome"] if calls else None},
                        "recovery_correctness": {"state": "not_exercised" if run.fault == "none" else "not_verified_here", "fault": run.fault},
                        "effect_safety": {"state": "unresolved" if unresolved else "pending_approval" if pending else "not_verified" if baseline else "observed_no_violation" if safe else "not_verified" if external else "not_exercised",
                                          "independent_truth": truth, "duplicate_operations": duplicates if truth else None,
                                          "unauthorized_effects": unauthorized, "unresolved_effects": unresolved,
                                          "pending_approvals": pending,
                                          "scope": "Recorded local mock operations only; not an OS isolation claim"},
                    }}
