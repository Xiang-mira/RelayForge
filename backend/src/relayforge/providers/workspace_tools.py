"""Controlled artifacts and file postconditions for managed sample workspaces."""

import hashlib
import difflib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from relayforge.journal.state import payload_hash
from relayforge.providers.mock_external import Evidence, InjectedCrash, KnownNoEffect
from relayforge.workspaces.fingerprint import fingerprint, manifest
from relayforge.workspaces.manager import Workspaces, reject_links
from relayforge.workspaces.sandbox import DockerSandbox
from relayforge.workspaces.snapshots import Snapshots


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    path: str = Field(min_length=1, max_length=200)


class WriteInput(ReadInput):
    content: str = Field(max_length=1_000_000)
    expected_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class DiffInput(ReadInput):
    original: str = Field(max_length=1_000_000)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class WorkspaceTools:
    def __init__(self, workspaces: Workspaces, artifacts: Path | None = None):
        self.workspaces = workspaces
        self.sandbox = DockerSandbox(workspaces)
        self.snapshots = Snapshots(workspaces)
        self.artifacts = artifacts or workspaces.root.parent / "workspace-receipts"
        reject_links(self.artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Opaque action key required")
        path = self.artifacts / (key + ".json")
        reject_links(path)
        return path

    def _save(self, key: str, record: dict):
        path = self._path(key)
        temporary = path.with_suffix("." + uuid4().hex + ".tmp")
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def validate(self, tool: str, branch_id: str, payload: dict):
        self.workspaces.branch_path(branch_id)
        if tool == "workspace.test":
            EmptyInput.model_validate(payload)
            return
        model = {
            "workspace.read": ReadInput,
            "workspace.write": WriteInput,
            "workspace.diff": DiffInput,
        }[tool]
        model.model_validate(payload)
        path = payload["path"]
        self.workspaces.file(branch_id, path)
        # v1 is a managed sample repository, not access to arbitrary host projects.
        if path not in ("calculator.py", "test_calculator.py"):
            raise ValueError("File is not part of the managed sample")
        if tool == "workspace.write" and path != "calculator.py":
            raise ValueError("Agent cannot rewrite the sample acceptance tests")

    def execute(
        self,
        tool: str,
        branch_id: str,
        payload: dict,
        key: str,
        fault: str = "none",
        *,
        context: dict | None = None,
    ) -> dict:
        self.validate(tool, branch_id, payload)
        existing = self._path(key)
        if existing.exists():
            record = json.loads(existing.read_text(encoding="utf-8"))
            if (
                record["payload_hash"] != payload_hash(payload)
                or record["tool"] != tool
                or record["branch_id"] != branch_id
            ):
                raise ValueError("Workspace artifact identity conflict")
            if record.get("completed"):
                return {"external_id": key, "provider": "managed_workspace"}
            raise ValueError("Incomplete workspace operation requires reconciliation")
        if tool == "workspace.write" and payload.get("expected_hash"):
            current_hash = hashlib.sha256(
                self.workspaces.file(branch_id, payload["path"]).read_bytes()
            ).hexdigest()
            if current_hash != payload["expected_hash"]:
                raise KnownNoEffect("Write rejected: inspected file version has changed")
        record = {
            "tool": tool,
            "branch_id": branch_id,
            "payload_hash": payload_hash(payload),
            "path": payload.get("path"),
            "completed": False,
            "context": context,
        }
        if tool == "workspace.write":
            record["content_hash"] = hashlib.sha256(payload["content"].encode()).hexdigest()
            record["before_snapshot_id"] = self.snapshots.capture(branch_id)
        self._save(key, record)  # Durable descriptor precedes any workspace operation.
        if tool == "workspace.read":
            result = {"content": self.workspaces.read(branch_id, payload["path"])}
        elif tool == "workspace.write":
            self.workspaces.write(branch_id, payload["path"], payload["content"])
            if fault == "after_workspace_write":
                raise InjectedCrash("Workspace write applied before artifact receipt")
            result = self.patch_evidence(record)
        elif tool == "workspace.diff":
            result = {"diff": self.workspaces.diff(branch_id, payload["path"], payload["original"])}
        else:
            snapshot_id = self.snapshots.capture(branch_id)
            source = self.snapshots.verified_source(snapshot_id)
            before = manifest(source)
            configuration = self.sandbox.test_configuration(branch_id=branch_id, resolve_image=True)
            # The container consumes the verified snapshot read-only, not a mutable branch.
            tested = self.sandbox.run_tests(
                branch_id,
                terminate=fault == "sandbox_terminated",
                snapshot_source=source,
                image_id=configuration["image_id"],
            )
            stable = (
                manifest(source) == before
                and manifest(self.workspaces.branch_path(branch_id)) == before
            )
            result = {
                "exit_code": tested.exit_code,
                "output": tested.output,
                "stdout": tested.stdout,
                "stderr": tested.stderr,
                "passed": tested.passed and stable,
                "timed_out": tested.timed_out,
                "source_hash": hashlib.sha256(
                    (source / "calculator.py").read_text(encoding="utf-8").encode()
                ).hexdigest(),
                "fault_injected": tested.fault_injected,
                "test_count": tested.test_count,
                "acceptance": {
                    "schema": 1,
                    **(context or {}),
                    "branch_id": branch_id,
                    "test_run_id": key,
                    "completed": not tested.timed_out and tested.test_count > 0,
                    "manifest": before,
                    "workspace_hash": fingerprint(before),
                    "configuration": configuration,
                    "configuration_hash": payload_hash(configuration),
                    "snapshot_id": snapshot_id,
                },
            }
        result["snapshot_id"] = (
            snapshot_id if tool == "workspace.test" else self.snapshots.capture(branch_id)
        )
        record.update(completed=True, result=result)
        self._save(key, record)
        if fault == "after_external_before_receipt":
            raise InjectedCrash("Workspace artifact committed before Journal receipt")
        return {"external_id": key, "provider": "managed_workspace"}

    def result(self, receipt: dict) -> dict:
        return self.artifact(receipt)["result"]

    def patch_evidence(self, record):
        result = {"content_hash": record["content_hash"]}
        before_id = record.get("before_snapshot_id")
        if before_id:
            before = self.snapshots.verified_source(before_id)
            after = self.workspaces.branch_path(record["branch_id"])
            diff = "".join(difflib.unified_diff(
                (before / record["path"]).read_text(encoding="utf-8").splitlines(True),
                (after / record["path"]).read_text(encoding="utf-8").splitlines(True),
                fromfile="before/" + record["path"], tofile="after/" + record["path"],
            ))
            result.update(before_snapshot_id=before_id, diff=diff[:32768],
                          diff_truncated=len(diff) > 32768,
                          workspace_hash=fingerprint(manifest(after)))
        return result

    def artifact(self, receipt: dict) -> dict:
        if receipt.get("provider") != "managed_workspace":
            raise ValueError("Receipt belongs to another provider")
        record = json.loads(self._path(receipt["external_id"]).read_text(encoding="utf-8"))
        if not record.get("completed"):
            raise ValueError("Artifact is incomplete")
        return {
            "tool": record["tool"],
            "branch_id": record["branch_id"],
            "result": record["result"],
        }

    def lookup(self, key: str, digest: str, *, quiescent=False) -> Evidence:
        path = self._path(key)
        if not path.exists():
            return Evidence("not_occurred" if quiescent else "indeterminate")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["payload_hash"] != digest:
            return Evidence("indeterminate")
        receipt = {"external_id": key, "provider": "managed_workspace"}
        if record.get("completed"):
            return Evidence("occurred", receipt)
        if record["tool"] == "workspace.write" and quiescent:
            current = self.workspaces.read(record["branch_id"], record["path"])
            if hashlib.sha256(current.encode()).hexdigest() == record["content_hash"]:
                record.update(
                    completed=True,
                    result={
                        **self.patch_evidence(record),
                        "snapshot_id": self.snapshots.capture(record["branch_id"]),
                    },
                )
                self._save(key, record)
                return Evidence("occurred", receipt)
        return Evidence("indeterminate")
