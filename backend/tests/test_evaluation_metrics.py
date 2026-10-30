import json

from relayforge.evaluation.metrics import fraction, summarize, unsafe_retries
from relayforge.evaluation.report import write_reports


def test_unsafe_retry_uses_chronology_and_protocol_guarantees_not_hidden_truth():
    calls = [
        {"monotonic_ns": 100, "idempotency_used": 0},
        {"monotonic_ns": 200, "idempotency_used": 0},
    ]
    assert unsafe_retries(calls, []) == 1
    assert unsafe_retries(calls, [150]) == 0
    assert unsafe_retries(calls, [50, 250]) == 1
    calls[1]["idempotency_used"] = 1
    assert unsafe_retries(calls, []) == 0
    assert unsafe_retries([{}, {}], []) is None
    assert fraction(0, 0)["value"] is None


def test_summary_preserves_unknown_undefined_metrics_and_unsupported_pair_exclusions():
    unresolved = {
        "mode": "journal",
        "template": "fixture",
        "fault": "no_query",
        "run_status": "NEEDS_REVIEW",
        "actual_external_effects": 1,
        "external_requests": 1,
        "unsafe_retry_count": 0,
        "asserted_external_outcome": None,
        "independent_acceptance": True,
        "reported_test_passed": True,
        "safe_unresolved": True,
        "recovery_eligible": False,
        "workflow_elapsed_s": 2.0,
    }
    report = summarize([unresolved])["modes"]["journal"]
    assert report["state_divergence_rate"] == {"numerator": 0, "denominator": 0, "value": None}
    assert report["false_success_rate"]["value"] is None
    assert report["coding_task_success"]["value"] == 0
    assert report["independently_accepted_code"]["value"] == 1
    assert report["safe_unresolved_cases"] == 1
    base = {**unresolved, "mode": "baseline", "probe_supported": False}
    assert (
        summarize([base, unresolved])["modes"]["journal"]["runtime_overhead"]["paired_cases"] == 0
    )


def test_raw_export_roundtrip_reproduces_statistics_and_preserves_undefined_values(tmp_path):
    record = {
        "mode": "full",
        "template": "unit-fixture",
        "fault": "unqueryable",
        "run_status": "NEEDS_REVIEW",
        "actual_external_effects": 1,
        "external_requests": 1,
        "unsafe_retry_count": None,
        "asserted_external_outcome": None,
        "independent_acceptance": True,
        "reported_test_passed": True,
        "workflow_elapsed_s": 1.0,
        "recovery_eligible": False,
    }
    original = write_reports(tmp_path / "first", [record], {"suite_id": "unit-only"})
    rows = [
        json.loads(line) for line in (tmp_path / "first/results.jsonl").read_text().splitlines()
    ]
    regenerated = write_reports(tmp_path / "second", rows, {"suite_id": "unit-only"})
    assert original == regenerated
    assert rows == [record]
    assert regenerated["modes"]["full"]["unsafe_retry_count"] is None
    assert (tmp_path / "first/results.csv").read_bytes() == (
        tmp_path / "second/results.csv"
    ).read_bytes()


def test_primary_comparison_does_not_hide_a_false_successful_fork_claim():
    row = {
        "mode": "full",
        "template": "unit-fixture",
        "fault": "fork_old_checkpoint",
        "run_id": "child",
        "run_status": "SUCCEEDED",
        "independent_acceptance": False,
        "reported_test_passed": True,
        "coding_run_id": "parent",
        "coding_run_status": "SUCCEEDED",
        "coding_independent_acceptance": True,
        "coding_reported_test_passed": True,
        "actual_external_effects": 1,
        "external_requests": 1,
        "unsafe_retry_count": 0,
        "workflow_elapsed_s": 1.0,
        "probe_supported": True,
        "probe": {"passed": False},
        "case_schema_version": 2,
    }
    result = summarize([row])["modes"]["full"]
    assert result["coding_task_success"]["value"] == 1
    assert result["false_success_rate"] == {"numerator": 1, "denominator": 2, "value": 0.5}
    assert result["false_success_all_cases"]["value"] == 1
    assert result["failed_probe_cases"] == 1
