"""Shared final coding-task acceptance; callers cannot substitute a success claim."""

from relayforge.journal.state import JournalError
from relayforge.models import Run, RunEvent


class TaskAcceptance:
    def __init__(self, executor):
        self.executor = executor

    def complete(self, run_id: str, branch_id: str, test_receipt: dict) -> str:
        tested = self.executor.workspace.result(test_receipt)
        passed = tested["passed"] and tested["exit_code"] == 0 and not tested["timed_out"]
        proof = self.executor.publications.require(run_id, branch_id) if passed else None
        if proof and proof["test_receipt"] != test_receipt:
            self.executor.publications.reject(run_id, "obsolete_test_receipt")
        result = "SUCCEEDED" if passed else "FAILED"
        with self.executor.journal.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            if not run or run.branch_id != branch_id:
                raise JournalError("Run/branch mismatch")
            if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
                result = "CANCELLED"
            run.status = result
            run.lease_until = None
            run.test_exit_code = tested["exit_code"]
            session.add(RunEvent(
                run_id=run.id, type="run.acceptance", data={
                    "result": result, "test_exit_code": tested["exit_code"],
                    "provider": "mock", "tested_version": proof,
                },
            ))
        return result
