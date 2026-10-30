from langgraph.graph import START
from sqlalchemy import select

from relayforge.journal.state import JournalError
from relayforge.models import Branch, Effect, EffectReference, Run, RunEvent, Task
from relayforge.runtime.graph import build_graph


class Forks:
    def __init__(self, executor, checkpointer):
        self.executor = executor
        self.sessions = executor.journal.sessions
        self.graph = build_graph(executor, checkpointer)
        self.snapshots = executor.workspace.snapshots

    def history(self, run_id: str):
        with self.sessions() as session:
            if not session.get(Run, run_id):
                raise JournalError("Run not found")
        return [
            {
                "checkpoint_id": item.config["configurable"]["checkpoint_id"],
                "stage": item.values.get("stage", "start"),
                "next": list(item.next),
                "snapshot_id": item.values.get("snapshot_id"),
                "created_at": item.created_at,
            }
            for item in self.graph.get_state_history({"configurable": {"thread_id": run_id}})
        ]

    def create(self, source_run_id: str, checkpoint_id: str) -> str:
        parent = self.graph.get_state(
            {"configurable": {"thread_id": source_run_id, "checkpoint_id": checkpoint_id}}
        )
        if not parent.values or parent.values.get("run_id") != source_run_id:
            raise JournalError("Checkpoint does not belong to source run")
        snapshot_id = parent.values.get("snapshot_id")
        if not snapshot_id:
            raise JournalError("Legacy checkpoint has no verified workspace snapshot")
        self.snapshots.verified_source(snapshot_id)
        with self.sessions.begin() as session:
            source = session.get(Run, source_run_id)
            if not source:
                raise JournalError("Source run not found")
            if source.mode != "full":
                raise JournalError(
                    "Fork isolation is unavailable in this explicit evaluation ablation"
                )
            template = session.get(Task, source.task_id).template
            branch = Branch(
                task_id=source.task_id,
                parent_id=source.branch_id,
                checkpoint_id=checkpoint_id,
                initial_snapshot_id=snapshot_id,
            )
            session.add(branch)
            session.flush()
            branch.workspace = str(self.executor.workspace.workspaces.branch_path(branch.id))
            run = Run(
                task_id=source.task_id,
                branch_id=branch.id,
                status="PREPARING",
                evaluation_run_id=source.evaluation_run_id,
            )
            session.add(run)
            session.flush()
            new_run, new_branch = run.id, branch.id
            own = session.scalars(
                select(Effect.effect_id).where(
                    Effect.branch_id == source.branch_id,
                    Effect.effect_class.in_(
                        ["IDEMPOTENT_WRITE", "NON_IDEMPOTENT_WRITE", "UNKNOWN_OUTCOME"]
                    ),
                    Effect.status.in_(["CONFIRMED", "IN_FLIGHT", "UNKNOWN"]),
                )
            ).all()
            inherited = session.scalars(
                select(EffectReference.effect_id).where(
                    EffectReference.branch_id == source.branch_id
                )
            ).all()
            for effect_id in set(own) | set(inherited):
                session.add(EffectReference(branch_id=new_branch, effect_id=effect_id))
        try:
            self.snapshots.restore(snapshot_id, new_branch)
            state = dict(parent.values)
            state.update(run_id=new_run, branch_id=new_branch, template=template, fault="none")
            state.pop("result", None)
            self.graph.update_state(
                {"configurable": {"thread_id": new_run}},
                state,
                as_node=parent.values.get("stage") or START,
            )
            with self.sessions.begin() as session:
                run = session.get(Run, new_run, with_for_update=True)
                run.status = (
                    "CANCELLED" if run.status in {"CANCELLED", "CANCEL_REQUESTED"} else "QUEUED"
                )
                session.add(
                    RunEvent(
                        run_id=new_run,
                        type="fork.created",
                        data={
                            "parent_run_id": source_run_id,
                            "checkpoint_id": checkpoint_id,
                            "snapshot_id": snapshot_id,
                            "historical_effects": "readonly",
                            "test_receipt": parent.values.get("test_receipt"),
                            "test_origin_run_id": (
                                self.executor.workspace.result(parent.values["test_receipt"])
                                .get("acceptance", {})
                                .get("run_id", source_run_id)
                                if parent.values.get("test_receipt")
                                else None
                            ),
                        },
                    )
                )
            return new_run
        except (ValueError, OSError, JournalError):
            with self.sessions.begin() as session:
                run = session.get(Run, new_run, with_for_update=True)
                run.status = (
                    "CANCELLED" if run.status in {"CANCELLED", "CANCEL_REQUESTED"} else "FAILED"
                )
                session.add(
                    RunEvent(
                        run_id=new_run,
                        type="fork.failed",
                        data={"reason": "snapshot_or_checkpoint_initialization"},
                    )
                )
            raise
