"""Persist a bounded mock suite for the normal local scheduler to execute."""

import subprocess

from sqlalchemy import select

from relayforge.evaluation.runner import ExperimentRunner
from relayforge.models import EvaluationRun


class EvaluationUnavailable(RuntimeError):
    pass


def enqueue(database, settings, templates, faults, repeats):
    if settings.model_provider != "mock":
        raise EvaluationUnavailable("Evaluations require the explicit no-cost mock provider")
    try:
        suite_id = ExperimentRunner(database, settings).create_suite(templates, faults, repeats)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        raise EvaluationUnavailable("Local sandbox/evaluation environment unavailable") from None
    with database.sessions() as session:
        identities = session.scalars(
            select(EvaluationRun.id).where(EvaluationRun.suite_id == suite_id)
        ).all()
    return {
        "suite_id": suite_id,
        "evaluation_run_ids": list(identities),
        "status": "QUEUED",
        "planned_observations": len(templates) * len(faults) * repeats * 3,
    }
