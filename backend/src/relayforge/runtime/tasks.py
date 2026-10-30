from pathlib import Path

from relayforge.evaluation.modes import ExperimentConfig
from relayforge.models import Branch, EvaluationRun, Run, RunEvent, Task
from relayforge.providers.templates import TEMPLATES
from relayforge.runtime.catalog import CONTROLLED_TEMPLATES
from relayforge.workspaces.manager import Workspaces
from relayforge.workspaces.snapshots import Snapshots


class Tasks:
    def __init__(self, sessions, workspaces: Workspaces, demo: Path):
        self.sessions = sessions
        self.workspaces = workspaces
        self.demo = demo

    def create(
        self,
        title: str,
        template="fix_addition",
        fault="none",
        *,
        mode="full",
        execution_config=None,
        evaluation_run_id=None,
        controlled=False,
    ) -> tuple[str, str]:
        if not title.strip() or len(title) > 200 or not (
            template in TEMPLATES or (controlled and template in CONTROLLED_TEMPLATES)
        ):
            raise ValueError("Invalid bounded sample task input")
        with self.sessions.begin() as session:
            task = Task(title=title, template=template)
            session.add(task)
            session.flush()
            task_id = task.id
        return task_id, self.start(
            task_id,
            fault,
            mode=mode,
            execution_config=execution_config,
            evaluation_run_id=evaluation_run_id,
            controlled=controlled,
        )

    def start(
        self,
        task_id: str,
        fault="none",
        *,
        mode="full",
        execution_config=None,
        evaluation_run_id=None,
        controlled=False,
    ) -> str:
        options = ExperimentConfig.model_validate(execution_config or {})
        if mode not in {"full", "journal", "baseline"} or (
            (mode != "full" or options != ExperimentConfig()) and not evaluation_run_id
        ):
            raise ValueError("Experimental configuration requires an evaluation identity")
        if fault not in {
            "none",
            "before_dispatch",
            "after_dispatch_before_call",
            "before_failure",
            "response_lost",
            "after_external_before_receipt",
            "sandbox_terminated",
        }:
            raise ValueError("Unsupported controlled fault")
        # Workspace preparation is not a runnable queued job until the copy completed.
        with self.sessions.begin() as session:
            if evaluation_run_id:
                evaluation = session.get(EvaluationRun, evaluation_run_id)
                if not evaluation or evaluation.mode != mode:
                    raise ValueError("Evaluation mode binding mismatch")
            task = session.get(Task, task_id)
            if not task:
                raise ValueError("Task not found")
            if task.template in CONTROLLED_TEMPLATES and not controlled:
                raise ValueError("Feedback tasks require the controlled task service")
            branch = Branch(task_id=task.id)
            session.add(branch)
            session.flush()
            branch.workspace = str(self.workspaces.branch_path(branch.id))
            run = Run(
                task_id=task_id,
                branch_id=branch.id,
                status="PREPARING",
                fault=fault,
                mode=mode,
                execution_config=options.model_dump() if evaluation_run_id else {},
                evaluation_run_id=evaluation_run_id,
            )
            session.add(run)
            session.flush()
            run_id, branch_id = run.id, branch.id
        try:
            source = (
                self.demo
                if task.template == "fix_addition"
                else self.demo.parent / task.template if task.template in CONTROLLED_TEMPLATES
                else self.demo.parent / "templates" / task.template
            )
            self.workspaces.create(branch_id, source)
            snapshot_id = Snapshots(self.workspaces).capture(branch_id)
        except (OSError, ValueError):
            with self.sessions.begin() as session:
                run = session.get(Run, run_id, with_for_update=True)
                run.status = (
                    "CANCELLED" if run.status in {"CANCELLED", "CANCEL_REQUESTED"} else "FAILED"
                )
                session.add(
                    RunEvent(
                        run_id=run_id,
                        type="run.preparation_failed",
                        data={"reason": "workspace_copy_failed"},
                    )
                )
            raise
        with self.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            run.status = (
                "CANCELLED" if run.status in {"CANCELLED", "CANCEL_REQUESTED"}
                else "CONTROLLED" if controlled else "QUEUED"
            )
            session.get(Branch, branch_id).initial_snapshot_id = snapshot_id
            session.add(
                RunEvent(
                    run_id=run_id,
                    type="run.prepared",
                    data={"provider": "mock", "status": run.status},
                )
            )
        return run_id
