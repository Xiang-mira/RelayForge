from relayforge.journal.state import JournalError
from relayforge.models import Run, RunEvent


class Cancellation:
    def __init__(self, sessions):
        self.sessions = sessions

    def request(self, run_id: str) -> str:
        with self.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            if not run:
                raise JournalError("Run not found")
            if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
                return run.status
            if run.status in {"SUCCEEDED", "FAILED"}:
                raise JournalError("Completed runs cannot be retroactively cancelled")
            run.status = (
                "CANCEL_REQUESTED" if run.status in {"RUNNING", "PREPARING"} else "CANCELLED"
            )
            session.add(
                RunEvent(
                    run_id=run_id,
                    type="run.cancel_requested",
                    data={
                        "status": run.status,
                        "semantics": "cooperative; dispatched effects are not rolled back",
                    },
                )
            )
            return run.status
