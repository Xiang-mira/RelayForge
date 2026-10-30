from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from relayforge.db import Base
from relayforge.journal.service import Journal
from relayforge.journal.state import Conflict, EffectClass, InvalidTransition, Status
from relayforge.models import Effect, RecoveryDecision, RunEvent

pytestmark = pytest.mark.postgres


def prepare(journal, ids, payload=None):
    return journal.prepare(
        *ids,
        "action-1",
        "mock.create",
        EffectClass.IDEMPOTENT_WRITE,
        payload or {"title": "hello"},
        "allow",
    )


def test_migration_matches_models(pg):
    engine, _ = pg
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert diff == []


def test_persistent_outcome_and_no_duplicate_dispatch(seeded):
    engine, sessions, ids = seeded
    journal = Journal(sessions)
    effect = prepare(journal, ids)
    assert journal.dispatch(effect.effect_id, True)
    journal.outcome(
        effect.effect_id,
        Status.CONFIRMED,
        "occurred",
        {"external_id": "receipt-1", "provider": "mock"},
    )
    engine.dispose()  # Force connection teardown/reconnection, not in-memory reuse.
    recovered = Journal(sessions)
    assert prepare(recovered, ids).effect_id == effect.effect_id
    assert recovered.get(effect.effect_id).external_receipt["external_id"] == "receipt-1"
    assert not recovered.dispatch(effect.effect_id, True)
    # A genuinely new Python process must recover the same PostgreSQL record.
    import json
    import os
    import subprocess
    import sys

    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    child_env = os.environ.copy()
    child_env["DATABASE_URL"] = engine.url.update_query_dict(
        {"options": "-csearch_path=" + schema}
    ).render_as_string(hide_password=False)
    program = """import json,sys
from relayforge.config import Settings
from relayforge.db import Database
from relayforge.journal.service import Journal
try:
    db=Database(Settings())
    effect=Journal(db.sessions).get(sys.stdin.read().strip())
    print(json.dumps({'status':effect.status,'receipt':effect.external_receipt}))
    db.close()
except Exception:
    sys.exit('Fresh-process persistence check failed (details suppressed)')
"""
    process = subprocess.run(
        [sys.executable, "-c", program],
        input=effect.effect_id,
        capture_output=True,
        text=True,
        env=child_env,
        check=False,
        timeout=15,
    )
    assert process.returncode == 0, "Fresh Python process could not read persisted effect"
    assert json.loads(process.stdout)["status"] == "CONFIRMED"
    assert json.loads(process.stdout)["receipt"]["external_id"] == "receipt-1"
    with pytest.raises(InvalidTransition):
        recovered.outcome(effect.effect_id, Status.FAILED, "not_occurred")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Effect)) == 1
        assert session.scalar(select(func.count()).select_from(RecoveryDecision)) == 1


def test_concurrent_intent_and_dispatch_are_single_claim(seeded):
    _, sessions, ids = seeded
    journal = Journal(sessions)
    with ThreadPoolExecutor(max_workers=8) as pool:
        effects = list(pool.map(lambda _: prepare(journal, ids), range(16)))
    assert len({effect.effect_id for effect in effects}) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: journal.dispatch(effects[0].effect_id, True), range(16)))
    assert sum(claims) == 1
    assert journal.get(effects[0].effect_id).attempt_count == 1


def test_conflict_is_audited_and_secret_values_not_persisted(seeded):
    _, sessions, ids = seeded
    journal = Journal(sessions)
    effect = prepare(journal, ids, {"api_key": "secret-never-store"})
    with pytest.raises(Conflict):
        prepare(journal, ids, {"api_key": "changed-secret"})
    assert "secret-never-store" not in str(journal.get(effect.effect_id).payload_summary)
    with sessions() as session:
        events = session.scalars(select(RunEvent)).all()
        assert any(event.type == "effect.conflict" for event in events)
        assert "secret-never-store" not in str([event.data for event in events])


def test_unknown_requires_evidence_and_denied_call_does_not_dispatch(seeded):
    _, sessions, ids = seeded
    journal = Journal(sessions)
    effect = prepare(journal, ids)
    assert not journal.dispatch(effect.effect_id, False)
    assert journal.get(effect.effect_id).status == Status.PREPARED
    assert journal.dispatch(effect.effect_id, True)
    journal.outcome(effect.effect_id, Status.UNKNOWN, None)
    with pytest.raises(InvalidTransition):
        journal.outcome(effect.effect_id, Status.FAILED, None)
    assert journal.get(effect.effect_id).status == Status.UNKNOWN


def test_database_rejects_invalid_status(seeded):
    engine, sessions, ids = seeded
    effect = prepare(Journal(sessions), ids)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("UPDATE effects SET status='MAGIC_SUCCESS' WHERE effect_id=:id"),
            {"id": effect.effect_id},
        )


def test_arbitrary_nonempty_object_cannot_be_used_as_a_success_receipt(seeded):
    from relayforge.journal.state import JournalError

    _, sessions, ids = seeded
    journal = Journal(sessions)
    effect = prepare(journal, ids)
    journal.dispatch(effect.effect_id, True)
    with pytest.raises(JournalError):
        journal.outcome(
            effect.effect_id, Status.CONFIRMED, "occurred", {"message": "pretend success"}
        )
    assert journal.get(effect.effect_id).status == Status.IN_FLIGHT
