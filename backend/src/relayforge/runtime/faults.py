from sqlalchemy import select

from relayforge.models import Run, RunEvent


def take_fault_once(sessions, run_id: str, requested: str, node="publish") -> str:
    if requested == "none":
        return "none"
    with sessions.begin() as session:
        run = session.get(Run, run_id, with_for_update=True)
        if not run:
            raise ValueError("Run not found")
        existing = session.scalar(
            select(RunEvent.id)
            .where(RunEvent.run_id == run_id, RunEvent.type == "fault.injected")
            .limit(1)
        )
        if existing:
            return "none"
        session.add(
            RunEvent(
                run_id=run_id,
                type="fault.injected",
                data={"fault": requested, "node": node, "one_shot": True},
            )
        )
        return requested
