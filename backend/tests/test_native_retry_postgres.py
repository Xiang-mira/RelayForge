from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select
from test_executor_postgres import effect_for, setup_executor

from relayforge.journal.state import Status
from relayforge.models import RunEvent
from relayforge.runtime.executor import OutcomeUnknown

pytestmark = pytest.mark.postgres


def prepare_uncertain(seeded, tmp_path):
    executor, ids = setup_executor(seeded, tmp_path, supports_query=False)
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "a", "mock.create", {"subject": "hello"}, "response_lost")
    effect = effect_for(executor, ids)
    ready = executor.reconcile(effect.effect_id, owner_stopped=True)
    assert ready.status == Status.UNKNOWN
    assert ready.reconciliation_result["outcome"] == "indeterminate"
    assert executor.can_resume_native(ready)
    return executor, ids, effect


def test_native_retry_keeps_unknown_until_real_receipt_and_cannot_be_taken_over(seeded, tmp_path):
    executor, ids, effect = prepare_uncertain(seeded, tmp_path)
    started, release = Event(), Event()
    execute = executor.external.execute

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return execute(*args, **kwargs)

    executor.external.execute = delayed
    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(executor.execute, *ids, "a", "mock.create", {"subject": "hello"})
        try:
            assert started.wait(3)
            assert executor.journal.get(effect.effect_id).status == Status.UNKNOWN
            with pytest.raises(OutcomeUnknown):
                executor.reconcile(effect.effect_id)
            with pytest.raises(OutcomeUnknown):
                executor.execute(*ids, "a", "mock.create", {"subject": "hello"})
        finally:
            release.set()
        assert first.result()["provider"] == "local_mock"
    final = executor.journal.get(effect.effect_id)
    assert final.status == Status.CONFIRMED and final.attempt_count == 2
    assert executor.external.truth_count(effect.idempotency_key) == 1
    assert executor.external.request_count(effect.idempotency_key) == 2
    with seeded[1]() as session:
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == ids[0])).all()
        assert sum(row.type == "effect.native_retransmitted" for row in events) == 1
        assert not any(row.data.get("status") == "FAILED" for row in events)


def test_rejection_of_native_retry_does_not_prove_original_absence(seeded, tmp_path):
    executor, ids, effect = prepare_uncertain(seeded, tmp_path)
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "a", "mock.create", {"subject": "hello"}, "before_failure")
    unknown = executor.journal.get(effect.effect_id)
    assert unknown.status == Status.UNKNOWN
    assert unknown.reconciliation_result["outcome"] == "indeterminate"
    assert executor.external.truth_count(effect.idempotency_key) == 1
    assert executor.external.request_count(effect.idempotency_key) == 2
    executor.reconcile(effect.effect_id, owner_stopped=True)
    assert executor.execute(*ids, "a", "mock.create", {"subject": "hello"})
    assert executor.journal.get(effect.effect_id).status == Status.CONFIRMED
    assert executor.external.truth_count(effect.idempotency_key) == 1
