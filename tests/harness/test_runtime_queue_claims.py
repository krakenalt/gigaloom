from __future__ import annotations

import concurrent.futures
from contextlib import closing
import sqlite3
import threading

import pytest

from gpt2giga_harness.performance_workloads.runtime.fixtures import seed_jobs
from gpt2giga_harness.performance_workloads.runtime.instrumentation import (
    TracingRuntimeStore,
)
from gpt2giga_harness.runtime.jobs import claims as claims_module
from gpt2giga_harness.runtime.jobs.claims import (
    CLAIM_CANDIDATE_WINDOW,
    CLAIM_MAX_CAS_RETRIES,
    _candidate_query,
)
from gpt2giga_harness.runtime.models import JobAttemptStatus, JobStatus
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore

WORKER_FINGERPRINT = {"os": "fixture", "harnesses": {}}


@pytest.mark.parametrize(
    ("excluded_before_end", "expected_job_id"),
    ((0, "job-09000"), (999, "job-09999")),
)
def test_claim_work_is_bounded_with_10k_jobs_and_90_percent_incompatible(
    tmp_path,
    excluded_before_end,
    expected_job_id,
):
    store = TracingRuntimeStore(tmp_path)
    seed_jobs(
        store,
        count=10_000,
        incompatible=9_000,
        excluded_before_end=excluded_before_end,
    )
    rows_before = store.claim_rows_snapshot()

    claim = store.claim_next_job(
        worker_id="worker-1",
        capability_fingerprint=WORKER_FINGERPRINT,
        lease_seconds=5,
    )

    assert claim is not None
    assert claim.job.id == expected_job_id
    assert store.claim_rows_snapshot() - rows_before <= CLAIM_CANDIDATE_WINDOW


@pytest.mark.parametrize("worker_count", (2, 8))
def test_parallel_claims_have_bounded_work_and_no_duplicates(tmp_path, worker_count):
    store = TracingRuntimeStore(tmp_path)
    seed_jobs(store, count=10_000)
    barrier = threading.Barrier(worker_count)
    rows_before = store.claim_rows_snapshot()

    def claim(worker_index: int) -> str:
        barrier.wait(timeout=5)
        result = store.claim_next_job(
            worker_id=f"worker-{worker_index}",
            capability_fingerprint=WORKER_FINGERPRINT,
            lease_seconds=5,
        )
        assert result is not None
        return result.job.id

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        claimed = list(executor.map(claim, range(worker_count)))

    assert len(claimed) == worker_count
    assert len(set(claimed)) == worker_count
    assert store.claim_rows_snapshot() - rows_before <= (
        worker_count * CLAIM_MAX_CAS_RETRIES * CLAIM_CANDIDATE_WINDOW
    )


def test_deep_fingerprint_pagination_does_not_starve_later_match(tmp_path):
    store = TracingRuntimeStore(tmp_path)
    for index in range(CLAIM_CANDIDATE_WINDOW * 2):
        store.submit_job(
            session_id=f"session-{index}",
            user_message_id=f"message-{index}",
            idempotency_key=f"incompatible-{index}",
            required_capability_fingerprint={
                "os": "fixture",
                "harnesses": {"missing-harness": {}},
            },
        )
    compatible = store.submit_job(
        session_id="session-compatible",
        user_message_id="message-compatible",
        idempotency_key="compatible",
    ).job
    rows_before = store.claim_rows_snapshot()

    claim = store.claim_next_job(
        worker_id="worker-1",
        capability_fingerprint=WORKER_FINGERPRINT,
        lease_seconds=5,
    )

    assert claim is not None
    assert claim.job.id == compatible.id
    assert store.claim_rows_snapshot() - rows_before <= (CLAIM_CANDIDATE_WINDOW * 3)


def test_parallel_interactive_claims_preserve_per_session_fifo(tmp_path):
    store = RuntimeCoordinationStore(tmp_path)
    first = store.submit_job(
        session_id="session-1",
        user_message_id="message-1",
        idempotency_key="interactive-first",
        origin="interactive",
    ).job
    second = store.submit_job(
        session_id="session-1",
        user_message_id="message-2",
        idempotency_key="interactive-second",
        origin="interactive",
    ).job
    barrier = threading.Barrier(2)

    def claim(worker_index: int):
        barrier.wait(timeout=5)
        return store.claim_next_job(
            worker_id=f"worker-{worker_index}",
            capability_fingerprint=WORKER_FINGERPRINT,
            lease_seconds=5,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, range(2)))

    claimed = [result for result in results if result is not None]
    assert [result.job.id for result in claimed] == [first.id]
    assert store.get_job(second.id).status is JobStatus.QUEUED

    first_claim = claimed[0]
    store.transition_attempt(
        first_claim.attempt.id,
        JobAttemptStatus.SUCCEEDED,
        expected_status=JobAttemptStatus.CLAIMED,
    )
    store.transition_job(
        first.id,
        JobStatus.SUCCEEDED,
        expected_status=JobStatus.RUNNING,
    )
    next_claim = store.claim_next_job(
        worker_id="worker-next",
        capability_fingerprint=WORKER_FINGERPRINT,
        lease_seconds=5,
    )
    assert next_claim is not None
    assert next_claim.job.id == second.id


def test_cancel_between_candidate_read_and_cas_prevents_claim(tmp_path, monkeypatch):
    store = RuntimeCoordinationStore(tmp_path)
    job = store.submit_job(
        session_id="session-1",
        user_message_id="message-1",
        idempotency_key="cancel-race",
    ).job
    candidate_read = threading.Event()
    cancellation_done = threading.Event()
    original_match = claims_module._fingerprint_matches

    def pause_after_candidate_read(*args, **kwargs):
        candidate_read.set()
        assert cancellation_done.wait(timeout=5)
        return original_match(*args, **kwargs)

    monkeypatch.setattr(
        claims_module, "_fingerprint_matches", pause_after_candidate_read
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            store.claim_next_job,
            worker_id="worker-1",
            capability_fingerprint=WORKER_FINGERPRINT,
            lease_seconds=5,
        )
        assert candidate_read.wait(timeout=5)
        store.request_cancel(job.id)
        cancellation_done.set()
        assert future.result(timeout=5) is None

    assert store.list_attempts(job.id) == ()
    assert store.get_job(job.id).cancel_requested_at is not None


def test_fingerprint_matching_does_not_hold_the_writer_lock(tmp_path, monkeypatch):
    store = RuntimeCoordinationStore(tmp_path)
    store.submit_job(
        session_id="session-claim",
        user_message_id="message-claim",
        idempotency_key="claim",
    )
    matching = threading.Event()
    writer_finished = threading.Event()
    original_match = claims_module._fingerprint_matches

    def wait_for_writer(*args, **kwargs):
        matching.set()
        assert writer_finished.wait(timeout=5)
        return original_match(*args, **kwargs)

    monkeypatch.setattr(claims_module, "_fingerprint_matches", wait_for_writer)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        claim_future = executor.submit(
            store.claim_next_job,
            worker_id="worker-1",
            capability_fingerprint=WORKER_FINGERPRINT,
            lease_seconds=5,
        )
        assert matching.wait(timeout=5)
        writer_future = executor.submit(
            store.submit_job,
            session_id="session-writer",
            user_message_id="message-writer",
            idempotency_key="writer",
        )
        try:
            submitted = writer_future.result(timeout=2)
        finally:
            writer_finished.set()

        claim = claim_future.result(timeout=5)

    assert submitted.created is True
    assert claim is not None


def test_queue_claim_query_uses_bounded_index(tmp_path):
    store = RuntimeCoordinationStore(tmp_path)
    sql, parameters = _candidate_query(
        now="2026-01-01T00:00:00+00:00",
        worker_os="fixture",
        available_harness_ids=(),
        cursor=None,
    )

    with closing(sqlite3.connect(store.path)) as connection:
        plan = connection.execute(f"EXPLAIN QUERY PLAN {sql}", parameters).fetchall()

    details = [str(row[3]) for row in plan]
    assert any("jobs_queue_claim_idx" in detail for detail in details)
    assert not any("SCAN candidate" in detail for detail in details)


def test_submit_job_normalizes_required_os_for_queue_filter(tmp_path):
    store = RuntimeCoordinationStore(tmp_path)
    job = store.submit_job(
        session_id="session-1",
        user_message_id="message-1",
        idempotency_key="required-os",
        required_capability_fingerprint={"os": "linux"},
    ).job

    with closing(sqlite3.connect(store.path)) as connection:
        required_os = connection.execute(
            "SELECT required_os FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()[0]

    assert required_os == "linux"
    assert (
        store.claim_next_job(
            worker_id="darwin-worker",
            capability_fingerprint={"os": "darwin", "harnesses": {}},
            lease_seconds=5,
        )
        is None
    )
