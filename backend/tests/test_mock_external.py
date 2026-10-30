from concurrent.futures import ThreadPoolExecutor

import pytest

from relayforge.providers.mock_external import InjectedCrash, KnownNoEffect, MockExternal


def test_external_commit_survives_lost_response_and_restart(tmp_path):
    path = tmp_path / "truth.sqlite3"
    external = MockExternal(path)
    with pytest.raises(TimeoutError):
        external.execute("action", "hash", "key", "response_lost")
    recovered = MockExternal(path)
    evidence = recovered.lookup("action", "hash")
    assert evidence.outcome == "occurred"
    assert recovered.execute("action", "hash", "key") == evidence.receipt
    assert recovered.truth_count("action") == 1
    assert recovered.request_count("action") == 2


def test_crash_window_and_before_failure_have_different_truth(tmp_path):
    external = MockExternal(tmp_path / "truth.sqlite3")
    with pytest.raises(KnownNoEffect):
        external.execute("before", "hash", "k1", "before_failure")
    assert external.truth_count("before") == 0
    with pytest.raises(InjectedCrash):
        external.execute("after", "hash", "k2", "after_external_before_receipt")
    assert external.truth_count("after") == 1


def test_no_query_or_unsettled_absence_is_honestly_unknown(tmp_path):
    external = MockExternal(tmp_path / "truth.sqlite3")
    assert external.lookup("missing", "hash").outcome == "indeterminate"
    assert external.lookup("missing", "hash", quiescent=True).outcome == "not_occurred"
    unsupported = MockExternal(external.path, supports_query=False)
    assert unsupported.lookup("missing", "hash", quiescent=True).outcome == "indeterminate"


def test_provider_protocol_rejects_changed_input(tmp_path):
    external = MockExternal(tmp_path / "truth.sqlite3")
    external.execute("a", "hash1", "key")
    with pytest.raises(KnownNoEffect):
        external.execute("a", "hash2", "key")
    assert external.truth_count("a") == 1
    assert external.lookup("a", "hash2").outcome == "indeterminate"


def test_concurrent_idempotency_and_independent_non_idempotent_counts(tmp_path):
    external = MockExternal(tmp_path / "truth.sqlite3")
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: external.execute("a", "hash", "key"), range(16)))
    assert len({receipt["external_id"] for receipt in receipts}) == 1
    non_idempotent = MockExternal(external.path, supports_idempotency=False)
    non_idempotent.execute("b", "hash", "ignored")
    non_idempotent.execute("b", "hash", "ignored")
    assert non_idempotent.truth_count("b") == 2
