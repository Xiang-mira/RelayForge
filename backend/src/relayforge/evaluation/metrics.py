"""Metrics derived from raw observations, with explicit undefined denominators."""

from collections import Counter
from itertools import pairwise
from statistics import median


def fraction(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def unsafe_retries(requests: list[dict], absence_evidence: list[int]) -> int | None:
    """Count actual subsequent non-idempotent calls without prior absence proof.

    Calls are ordered by independent provider request ordinal. Native idempotency
    is a valid safety guarantee. Hidden truth/fault labels are never retry evidence.
    Missing chronology cannot be silently treated as a measured zero.
    """
    unsafe = 0
    for previous, current in pairwise(requests):
        if current.get("idempotency_used") == 1:
            continue
        if (
            current.get("idempotency_used") is None
            or not previous.get("monotonic_ns")
            or not current.get("monotonic_ns")
        ):
            return None
        start, end = int(previous["monotonic_ns"]), int(current["monotonic_ns"])
        if not any(start < int(proof) < end for proof in absence_evidence):
            unsafe += 1
    return unsafe


def state_divergence(row: dict) -> bool | None:
    asserted = row.get("asserted_external_outcome")
    if asserted == "occurred":
        return row["actual_external_effects"] < 1
    if asserted == "not_occurred":
        return row["actual_external_effects"] != 0
    return None


def accepted(row: dict) -> bool:
    return bool(
        row.get("coding_run_status", row["run_status"]) == "SUCCEEDED"
        and row.get("coding_independent_acceptance", row.get("independent_acceptance"))
        and row.get("coding_reported_test_passed", row.get("reported_test_passed"))
    )


def claims(row):
    values = [
        (
            row["run_status"],
            bool(row.get("independent_acceptance") and row.get("reported_test_passed")),
        )
    ]
    if row.get("coding_run_id") and row["coding_run_id"] != row.get("run_id"):
        values.append(
            (
                row["coding_run_status"],
                bool(row["coding_independent_acceptance"] and row["coding_reported_test_passed"]),
            )
        )
    return values


def summarize(records: list[dict]) -> dict:
    modes = sorted({row["mode"] for row in records})
    result = {
        "recorded_cases": len(records),
        "metrics_schema_version": 2,
        "observation_schema_versions": sorted(
            {row.get("case_schema_version", 1) for row in records}
        ),
        "modes": {},
    }
    valid = [row for row in records if not row.get("infrastructure_error")]
    for mode in modes:
        all_rows = [row for row in records if row["mode"] == mode]
        rows = [row for row in valid if row["mode"] == mode]
        succeeded = [
            claim_valid
            for row in rows
            for status, claim_valid in claims(row)
            if status == "SUCCEEDED"
        ]
        comparable = [row for row in rows if state_divergence(row) is not None]
        recovery = [row for row in rows if row.get("recovery_eligible")]
        retry_values = [row.get("unsafe_retry_count") for row in rows]
        unsupported = [row for row in rows if row.get("probe_supported") is False]
        result["modes"][mode] = {
            "recorded_cases": len(all_rows),
            "executed_cases": len(rows),
            "infrastructure_failed_cases": len(all_rows) - len(rows),
            "cases_by_fault": dict(Counter(row["fault"] for row in rows)),
            "duplicate_effect_count": sum(
                max(row["actual_external_effects"] - 1, 0) for row in rows
            ),
            "unsafe_retry_count": sum(retry_values)
            if all(value is not None for value in retry_values)
            else None,
            "unsafe_retry_unmeasured_cases": sum(value is None for value in retry_values),
            "external_request_count": sum(row["external_requests"] for row in rows),
            "actual_external_effect_count": sum(row["actual_external_effects"] for row in rows),
            "recovery_correctness": fraction(
                sum(bool(row.get("recovery_correct")) for row in recovery), len(recovery)
            ),
            "state_divergence_rate": fraction(
                sum(state_divergence(row) is True for row in comparable), len(comparable)
            ),
            "unasserted_external_outcomes": len(rows) - len(comparable),
            "manual_review_rate": fraction(
                sum(
                    row.get("coding_run_status", row["run_status"]) == "NEEDS_REVIEW"
                    for row in rows
                ),
                len(rows),
            ),
            "manual_reviews_by_fault": dict(
                Counter(
                    row["fault"]
                    for row in rows
                    if row.get("coding_run_status", row["run_status"]) == "NEEDS_REVIEW"
                )
            ),
            "safe_unresolved_cases": sum(bool(row.get("safe_unresolved")) for row in rows),
            "unsupported_probe_cases": len(unsupported),
            "failed_probe_cases": sum(
                row.get("probe", {}).get("passed") is False
                for row in rows
                if row.get("probe_supported") is not False
            ),
            "coding_task_success": fraction(sum(accepted(row) for row in rows), len(rows)),
            "independently_accepted_code": fraction(
                sum(
                    bool(
                        row.get("coding_independent_acceptance", row.get("independent_acceptance"))
                    )
                    for row in rows
                ),
                len(rows),
            ),
            "false_success_rate": fraction(sum(not valid for valid in succeeded), len(succeeded)),
            "false_success_all_cases": fraction(
                sum(
                    any(status == "SUCCEEDED" and not valid for status, valid in claims(row))
                    for row in rows
                ),
                len(rows),
            ),
            "workflow_elapsed_median_s": median([row["workflow_elapsed_s"] for row in rows])
            if rows
            else None,
            "runtime_overhead": {"paired_cases": 0, "median_ms": None, "median_ratio": None},
        }

    def identity(row):
        return row["template"], row["fault"], row.get("seed", 0), row.get("repeat", 0)

    baseline = {
        identity(row): row
        for row in valid
        if row["mode"] == "baseline" and row.get("probe_supported") is not False
    }
    for mode in modes:
        if mode == "baseline":
            continue
        pairs = [
            (baseline[identity(row)], row)
            for row in valid
            if row["mode"] == mode
            and identity(row) in baseline
            and row.get("probe_supported") is not False
        ]
        pairs = [(base, row) for base, row in pairs if base["workflow_elapsed_s"] > 0]
        if pairs:
            result["modes"][mode]["runtime_overhead"] = {
                "paired_cases": len(pairs),
                "median_ms": median(
                    (row["workflow_elapsed_s"] - base["workflow_elapsed_s"]) * 1000
                    for base, row in pairs
                ),
                "median_ratio": median(
                    row["workflow_elapsed_s"] / base["workflow_elapsed_s"] for base, row in pairs
                ),
                "scope": "same template/fault/seed/repeat, supported probes; excludes setup and independent evaluator tests",
            }
    return result
