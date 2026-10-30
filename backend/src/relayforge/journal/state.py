import hashlib
import json
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5


class Status(StrEnum):
    PREPARED = "PREPARED"
    IN_FLIGHT = "IN_FLIGHT"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class EffectClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    WORKSPACE_MUTATION = "WORKSPACE_MUTATION"
    IDEMPOTENT_WRITE = "IDEMPOTENT_WRITE"
    NON_IDEMPOTENT_WRITE = "NON_IDEMPOTENT_WRITE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class JournalError(Exception):
    pass


class RunCancelled(JournalError):
    pass


class Conflict(JournalError):
    """Same logical action with a different tool, classification or input."""


class InvalidTransition(JournalError):
    pass


def validate_transition(current: str, target: str, evidence: str | None = None) -> None:
    if (current, target) == (Status.PREPARED, Status.IN_FLIGHT):
        return
    if current in (Status.IN_FLIGHT, Status.UNKNOWN):
        if target == Status.UNKNOWN:
            return
        if target == Status.CONFIRMED and evidence == "occurred":
            return
        if target == Status.FAILED and evidence == "not_occurred":
            return
    if (current, target) == (Status.FAILED, Status.PREPARED) and evidence == "not_occurred":
        return
    raise InvalidTransition(f"Refused transition {current} -> {target}: insufficient evidence")


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summary(payload: dict) -> dict:
    # No input value or arbitrary key is persisted. Even keys can contain secrets.
    return {
        "field_count": len(payload),
        "value_types": sorted(type(v).__name__ for v in payload.values()),
    }


def logical_action_id(plan_step: str) -> str:
    # Stable across recovery and parameter regeneration; never derived from payload.
    return str(uuid5(NAMESPACE_URL, "relayforge/plan-step/" + plan_step))


def idempotency_key(branch_id: str, action_id: str) -> str:
    return hashlib.sha256(f"{branch_id}/{action_id}".encode()).hexdigest()
