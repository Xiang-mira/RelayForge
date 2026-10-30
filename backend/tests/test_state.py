import itertools

import pytest

from relayforge.journal.state import (
    InvalidTransition,
    Status,
    idempotency_key,
    logical_action_id,
    payload_hash,
    summary,
    validate_transition,
)


@pytest.mark.parametrize("current,target", list(itertools.product(Status, repeat=2)))
def test_no_evidence_cannot_claim_success_or_failure(current, target):
    allowed = (current, target) in {
        (Status.PREPARED, Status.IN_FLIGHT),
        (Status.IN_FLIGHT, Status.UNKNOWN),
        (Status.UNKNOWN, Status.UNKNOWN),
    }
    if allowed:
        validate_transition(current, target)
    else:
        with pytest.raises(InvalidTransition):
            validate_transition(current, target)


@pytest.mark.parametrize("source", [Status.IN_FLIGHT, Status.UNKNOWN])
def test_evidence_has_direction(source):
    validate_transition(source, Status.CONFIRMED, "occurred")
    validate_transition(source, Status.FAILED, "not_occurred")
    with pytest.raises(InvalidTransition):
        validate_transition(source, Status.FAILED, "occurred")
    with pytest.raises(InvalidTransition):
        validate_transition(source, Status.CONFIRMED, "not_occurred")


def test_confirmed_is_terminal_and_retry_requires_absence():
    for target in Status:
        for evidence in (None, "occurred", "not_occurred"):
            with pytest.raises(InvalidTransition):
                validate_transition(Status.CONFIRMED, target, evidence)
    validate_transition(Status.FAILED, Status.PREPARED, "not_occurred")


def test_hash_identity_and_confidential_summary():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})
    action = logical_action_id("notify-1")
    assert action == logical_action_id("notify-1")
    assert idempotency_key("branch1", action) != idempotency_key("branch2", action)
    assert "super-secret" not in str(summary({"super-secret-key": "super-secret-value"}))
    with pytest.raises(ValueError):
        payload_hash({"x": float("nan")})
