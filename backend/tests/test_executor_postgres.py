import pytest

from relayforge.journal.service import Journal
from relayforge.journal.state import Conflict, Status
from relayforge.providers.mock_external import InjectedCrash, MockExternal
from relayforge.runtime.executor import (
    ApprovalRequired,
    OutcomeUnknown,
    PermissionDenied,
    ToolExecutor,
)

pytestmark = pytest.mark.postgres


def setup_executor(seeded, tmp_path, **kwargs):
    _, sessions, ids = seeded
    external = MockExternal(tmp_path / "external.sqlite3", **kwargs)
    return ToolExecutor(Journal(sessions), external), ids


def effect_for(executor, ids, action="a"):
    from sqlalchemy import select

    from relayforge.models import Effect

    with executor.journal.sessions() as session:
        return session.scalar(
            select(Effect).where(Effect.branch_id == ids[1], Effect.logical_action_id == action)
        )


def test_denied_and_approval_gates_prevent_side_effects(seeded, tmp_path):
    executor, ids = setup_executor(seeded, tmp_path)
    with pytest.raises(PermissionDenied):
        executor.execute(*ids, "deny", "real.external_write", {"subject": "hello"})
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, "a", "mock.notify", {"subject": "hello"})
    effect = effect_for(executor, ids)
    assert executor.external.truth_count(effect.idempotency_key) == 0
    executor.approvals.decide(effect.effect_id, "APPROVED")
    first = executor.execute(*ids, "a", "mock.notify", {"subject": "hello"})
    assert executor.execute(*ids, "a", "mock.notify", {"subject": "hello"}) == first
    assert executor.external.truth_count(effect.idempotency_key) == 1
    assert executor.external.request_count(effect.idempotency_key) == 1


@pytest.mark.parametrize("fault", ["response_lost", "after_external_before_receipt"])
def test_loss_and_crash_reconcile_without_duplicate(seeded, tmp_path, fault):
    executor, ids = setup_executor(seeded, tmp_path)
    with pytest.raises((OutcomeUnknown, InjectedCrash)):
        executor.execute(*ids, "a", "mock.create", {"subject": "hello"}, fault)
    effect = effect_for(executor, ids)
    assert executor.external.truth_count(effect.idempotency_key) == 1
    assert executor.external.request_count(effect.idempotency_key) == 1
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "a", "mock.create", {"subject": "hello"})
    recovered = executor.reconcile(effect.effect_id, owner_stopped=True)
    assert recovered.status == Status.CONFIRMED
    executor.execute(*ids, "a", "mock.create", {"subject": "hello"})
    assert executor.external.truth_count(effect.idempotency_key) == 1


def test_unsupported_query_remains_unknown(seeded, tmp_path):
    executor, ids = setup_executor(seeded, tmp_path, supports_query=False)
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "a", "mock.create", {"subject": "hello"}, "response_lost")
    effect = effect_for(executor, ids)
    assert executor.reconcile(effect.effect_id, owner_stopped=True).status == Status.UNKNOWN
    assert executor.external.truth_count(effect.idempotency_key) == 1


def test_changed_input_cannot_reach_provider(seeded, tmp_path):
    executor, ids = setup_executor(seeded, tmp_path)
    executor.execute(*ids, "a", "mock.create", {"subject": "hello"})
    with pytest.raises(Conflict):
        executor.execute(*ids, "a", "mock.create", {"subject": "changed"})
    assert executor.external.truth_count(effect_for(executor, ids).idempotency_key) == 1


def test_query_outage_pauses_without_native_retry_or_leaking_error(seeded, tmp_path, monkeypatch):
    executor, ids = setup_executor(seeded, tmp_path)
    with pytest.raises(InjectedCrash):
        executor.execute(*ids, "a", "mock.create", {"subject": "hello"}, "after_external_before_receipt")
    effect = effect_for(executor, ids)

    def unavailable(*args, **kwargs):
        raise TimeoutError("private-provider-credential-must-not-be-recorded")

    with monkeypatch.context() as patch:
        patch.setattr(executor.external, "lookup", unavailable)
        recovered = executor.reconcile(effect.effect_id, owner_stopped=True)
        assert recovered.status == Status.UNKNOWN and recovered.external_receipt is None
        assert recovered.reconciliation_method == "provider_query_unavailable"
        assert not executor.can_resume_native(recovered)
        with pytest.raises(OutcomeUnknown):
            executor.execute(*ids, "a", "mock.create", {"subject": "hello"})
        assert "private-provider" not in str(recovered.reconciliation_result)
    assert executor.external.request_count(effect.idempotency_key) == 1
    assert executor.external.truth_count(effect.idempotency_key) == 1
    confirmed = executor.reconcile(effect.effect_id, owner_stopped=True)
    assert confirmed.status == Status.CONFIRMED
    executor.execute(*ids, "a", "mock.create", {"subject": "hello"})
    assert executor.external.request_count(effect.idempotency_key) == 1


def test_ambiguous_provider_records_are_not_a_success_receipt(seeded, tmp_path):
    executor, ids = setup_executor(seeded, tmp_path, supports_idempotency=False)
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, "a", "mock.notify", {"subject": "hello"})
    effect = effect_for(executor, ids)
    executor.approvals.decide(effect.effect_id, "APPROVED")
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "a", "mock.notify", {"subject": "hello"}, "response_lost")
    # Deliberately inconsistent independent provider fixture, not a Runtime retry.
    executor.external.execute(effect.idempotency_key, effect.payload_hash, None)
    recovered = executor.reconcile(effect.effect_id, owner_stopped=True)
    assert recovered.status == Status.UNKNOWN and recovered.external_receipt is None
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "a", "mock.notify", {"subject": "hello"})
    assert executor.external.request_count(effect.idempotency_key) == 2
    assert executor.external.truth_count(effect.idempotency_key) == 2


def test_active_dispatch_cannot_be_taken_over(seeded, tmp_path):
    executor, ids = setup_executor(seeded, tmp_path)
    with pytest.raises(InjectedCrash):
        executor.execute(
            *ids, "a", "mock.create", {"subject": "hello"}, "after_dispatch_before_call"
        )
    effect = effect_for(executor, ids)
    with pytest.raises(OutcomeUnknown):
        executor.reconcile(effect.effect_id)
    assert executor.reconcile(effect.effect_id, owner_stopped=True).status == Status.FAILED


def test_non_idempotent_response_loss_is_not_masked_by_provider_deduplication(seeded, tmp_path):
    executor, ids = setup_executor(seeded, tmp_path, supports_idempotency=False)
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, "a", "mock.notify", {"subject": "hello"})
    effect = effect_for(executor, ids)
    executor.approvals.decide(effect.effect_id, "APPROVED")
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "a", "mock.notify", {"subject": "hello"}, "response_lost")
    for _ in range(3):
        with pytest.raises(OutcomeUnknown):
            executor.execute(*ids, "a", "mock.notify", {"subject": "hello"})
    executor.reconcile(effect.effect_id, owner_stopped=True)
    executor.execute(*ids, "a", "mock.notify", {"subject": "hello"})
    assert executor.external.request_count(effect.idempotency_key) == 1
    assert executor.external.truth_count(effect.idempotency_key) == 1


def test_managed_read_and_write_are_journaled_and_reuse_receipts(seeded, tmp_path):
    from relayforge.providers.workspace_tools import WorkspaceTools
    from relayforge.workspaces.manager import Workspaces

    executor, ids = setup_executor(seeded, tmp_path)
    source = tmp_path / "sample"
    source.mkdir()
    (source / "calculator.py").write_text("def add(a,b): return a-b\n")
    workspaces = Workspaces(tmp_path / "workspaces")
    workspaces.create(ids[1], source)
    executor.workspace = WorkspaceTools(workspaces)
    receipt = executor.execute(*ids, "read", "workspace.read", {"path": "calculator.py"})
    write = {"path": "calculator.py", "content": "def add(a,b): return a+b\n"}
    executor.execute(*ids, "write", "workspace.write", write)
    replay = executor.execute(*ids, "read", "workspace.read", {"path": "calculator.py"})
    assert replay == receipt
    assert "a-b" in executor.workspace.result(replay)["content"]
    assert "a+b" in workspaces.read(ids[1], "calculator.py")
    assert effect_for(executor, ids, "read").effect_class == "READ_ONLY"
    assert effect_for(executor, ids, "write").effect_class == "WORKSPACE_MUTATION"
