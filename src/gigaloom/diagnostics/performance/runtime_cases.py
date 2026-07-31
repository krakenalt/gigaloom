"""Bounded, content-free G6 durable runtime performance profiling."""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Final, Mapping

try:
    import resource
except ModuleNotFoundError:  # pragma: no cover - exercised on Windows
    resource = None

from gigaloom.config import HarnessConfig
from gigaloom.runtime.api import (
    JobAttemptStatus,
    RuntimeCoordinationStore,
)
from gigaloom.runtime.reconcile import RuntimeReconciler
from gigaloom.runtime.worker import (
    DEFAULT_MAX_IDLE_SECONDS,
    DEFAULT_POLL_SECONDS,
    DurableJobWorker,
)
from gigaloom.sessions import FilesystemHarnessSessionStore

from .runtime import (
    _OperationSample,
    _TracingRuntimeStore,
)
from .runtime_metrics import _measure
from .runtime_surfaces import _fixture_registry


SCHEMA_VERSION: Final[str] = "gigaloom.runtime-performance-profile.v3"
FIXTURE_SET_VERSION: Final[str] = "g6-02.v1"
MAX_SAMPLES: Final[int] = 100
QUEUE_SCALE: Final[int] = 16
RUN_UPDATE_SCALE: Final[int] = 16
IDLE_WINDOW_SECONDS: Final[float] = 0.56
IDLE_POLL_SECONDS: Final[float] = DEFAULT_POLL_SECONDS
IDLE_MAX_SECONDS: Final[float] = DEFAULT_MAX_IDLE_SECONDS
LOCK_HOLD_SECONDS: Final[float] = 0.015
MAX_IDLE_CYCLES_PER_MINUTE: Final[float] = 65.0
MAX_WAKE_LATENCY_MS: Final[float] = 250.0

REQUIRED_COVERAGE: Final[dict[str, tuple[str, ...]]] = {
    "resources": (
        "worker_idle_cycle",
        "worker_idle_loop",
        "worker_wakeup_signal",
        "worker_active_echo",
    ),
    "sqlite_and_queue": (
        "queue_claim_one",
        "queue_claim_many",
        "sqlite_lock_contention",
    ),
    "worker_lifecycle": (
        "worker_startup",
        "schedule_scan_empty",
        "worker_heartbeat",
        "retry_requeue",
        "cancel_request",
        "expired_lease_recovery",
        "runtime_reconcile",
        "worker_shutdown",
    ),
    "delivery_and_surfaces": (
        "web_app_startup",
        "api_defaults",
        "api_session_events",
        "sse_terminal_attach",
        "web_payload_projection",
        "tui_navigation_load",
    ),
    "filesystem": ("session_run_update",),
}


def _profile_queue_and_lifecycle(root: Path) -> list[_OperationSample]:
    root.mkdir(parents=True)
    store = _TracingRuntimeStore(root / "main")
    fingerprint: dict[str, Any] = {}
    store.register_worker(
        worker_id="fixture-lifecycle",
        process_id=os.getpid(),
        hostname="fixture",
        capability_fingerprint=fingerprint,
    )

    one = store.submit_job(
        session_id="fixture-one",
        user_message_id="fixture-one",
        initial_run_id="fixture-one",
        idempotency_key="fixture-one",
    ).job
    claim_one = _measure(
        "queue_claim_one",
        lambda: _claim_one(store, one.id, fingerprint),
        store,
    )

    fairness_store = _TracingRuntimeStore(root / "fairness")
    claim_many = _measure(
        "queue_claim_many",
        lambda: _claim_many(fairness_store, fingerprint),
        fairness_store,
    )

    schedule_worker = DurableJobWorker(
        HarnessConfig(data_dir=root / "schedule"),
        registry=_fixture_registry(),
        worker_id="fixture-schedule",
    )
    schedule_store = _TracingRuntimeStore(root / "schedule")
    schedule_worker.runtime_store = schedule_store
    schedule_scan = _measure(
        "schedule_scan_empty",
        lambda: _schedule_scan(schedule_worker),
        schedule_store,
    )
    heartbeat = _measure(
        "worker_heartbeat",
        lambda: _heartbeat(store),
        store,
    )
    retry = _measure(
        "retry_requeue",
        lambda: _retry_requeue(store, fingerprint),
        store,
    )
    cancel = _measure(
        "cancel_request",
        lambda: _cancel_request(store),
        store,
    )
    recovery = _measure(
        "expired_lease_recovery",
        lambda: _expired_lease_recovery(store),
        store,
    )
    reconcile_store = _TracingRuntimeStore(root / "reconcile")
    sessions = FilesystemHarnessSessionStore(root / "reconcile")
    reconcile = _measure(
        "runtime_reconcile",
        lambda: _reconcile(reconcile_store, sessions),
        reconcile_store,
    )
    contention = _measure(
        "sqlite_lock_contention",
        lambda: _sqlite_lock_contention(store),
        store,
    )
    return [
        claim_one,
        claim_many,
        schedule_scan,
        heartbeat,
        retry,
        cancel,
        recovery,
        reconcile,
        contention,
    ]


def _claim_one(
    store: RuntimeCoordinationStore,
    expected_job_id: str,
    fingerprint: Mapping[str, Any],
) -> Mapping[str, float]:
    claim = store.claim_next_job(
        worker_id="fixture-one",
        capability_fingerprint=fingerprint,
        lease_seconds=5,
    )
    if claim is None or claim.job.id != expected_job_id:
        raise RuntimeError("single queue claim fixture failed")
    return {"claimed_jobs": 1.0}


def _claim_many(
    store: RuntimeCoordinationStore,
    fingerprint: Mapping[str, Any],
) -> Mapping[str, float]:
    for index in range(QUEUE_SCALE):
        store.submit_job(
            session_id=f"fixture-many-{index:02d}",
            user_message_id=f"fixture-many-{index:02d}",
            initial_run_id=f"fixture-many-{index:02d}",
            idempotency_key=f"fixture-many-{index:02d}",
        )
    barrier = threading.Barrier(2)

    def claim_all(worker_id: str) -> list[str]:
        barrier.wait(timeout=2)
        claimed: list[str] = []
        while True:
            item = store.claim_next_job(
                worker_id=worker_id,
                capability_fingerprint=fingerprint,
                lease_seconds=5,
            )
            if item is None:
                return claimed
            claimed.append(item.job.id)

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(claim_all, "fixture-many-a"),
            executor.submit(claim_all, "fixture-many-b"),
        ]
        claims = [future.result(timeout=10) for future in futures]
    elapsed = max(time.perf_counter() - started, 0.000_001)
    flattened = [job_id for worker_claims in claims for job_id in worker_claims]
    if len(flattened) != QUEUE_SCALE or len(set(flattened)) != QUEUE_SCALE:
        raise RuntimeError("parallel queue fixture lost or duplicated a claim")
    counts = [len(item) for item in claims]
    return {
        "claimed_jobs": float(len(flattened)),
        "jobs_per_second": len(flattened) / elapsed,
        "worker_a_claims": float(counts[0]),
        "worker_b_claims": float(counts[1]),
        "claim_imbalance": float(abs(counts[0] - counts[1])),
        "duplicate_claims": 0.0,
    }


def _schedule_scan(worker: DurableJobWorker) -> Mapping[str, float]:
    worker._trigger_schedules()
    return {"due_occurrences": 0.0}


def _heartbeat(store: RuntimeCoordinationStore) -> Mapping[str, float]:
    store.heartbeat_worker("fixture-lifecycle")
    return {"heartbeats": 1.0}


def _retry_requeue(
    store: RuntimeCoordinationStore,
    fingerprint: Mapping[str, Any],
) -> Mapping[str, float]:
    job = store.submit_job(
        session_id="fixture-retry",
        user_message_id="fixture-retry",
        initial_run_id="fixture-retry",
        idempotency_key="fixture-retry",
        max_attempts=2,
    ).job
    claim = store.claim_next_job(
        worker_id="fixture-retry",
        capability_fingerprint=fingerprint,
        lease_seconds=5,
    )
    if claim is None or claim.job.id != job.id:
        raise RuntimeError("retry fixture could not claim its job")
    attempt = store.set_attempt_idempotency_class(claim.attempt.id, "read_only")
    store.transition_attempt(
        attempt.id,
        JobAttemptStatus.RUNNING,
        expected_status=JobAttemptStatus.CLAIMED,
    )
    _, retrying = store.finish_attempt(
        attempt.id,
        JobAttemptStatus.FAILED,
        error_summary="content-free retry fixture",
        retry_delay_seconds=0,
        sync_terminal_run=False,
    )
    requeued = store.requeue_due_jobs()
    if retrying.status.value != "retry_wait" or requeued < 1:
        raise RuntimeError("retry fixture did not requeue")
    return {"jobs_requeued": float(requeued)}


def _cancel_request(store: RuntimeCoordinationStore) -> Mapping[str, float]:
    job = store.submit_job(
        session_id="fixture-cancel",
        user_message_id="fixture-cancel",
        initial_run_id="fixture-cancel",
        idempotency_key="fixture-cancel",
    ).job
    canceled = store.request_cancel(job.id)
    if canceled.cancel_requested_at is None:
        raise RuntimeError("cancel fixture was not persisted")
    return {"cancel_requests": 1.0}


def _expired_lease_recovery(store: RuntimeCoordinationStore) -> Mapping[str, float]:
    job = store.submit_job(
        session_id="fixture-recovery",
        user_message_id="fixture-recovery",
        initial_run_id="fixture-recovery",
        idempotency_key="fixture-recovery",
        max_attempts=2,
    ).job
    store.create_attempt(
        job.id,
        run_id="fixture-recovery",
        lease_owner="fixture-orphan",
        leased_until="2000-01-01T00:00:00+00:00",
        idempotency_class="read_only",
    )
    recovered = store.recover_expired_attempts(retry_delay_seconds=0)
    if not recovered:
        raise RuntimeError("expired lease fixture was not recovered")
    return {"attempts_recovered": float(len(recovered))}


def _reconcile(
    store: RuntimeCoordinationStore,
    sessions: FilesystemHarnessSessionStore,
) -> Mapping[str, float]:
    report = RuntimeReconciler(store, sessions).reconcile()
    if report.outbox_failed:
        raise RuntimeError("reconcile fixture produced an unexpected outbox failure")
    return {
        "runs_scanned": float(report.runs_scanned),
        "jobs_repaired": float(report.jobs_repaired),
        "attempts_repaired": float(report.attempts_repaired),
        "outbox_processed": float(report.outbox_processed),
        "outbox_failed": float(report.outbox_failed),
    }


def _sqlite_lock_contention(store: _TracingRuntimeStore) -> Mapping[str, float]:
    blocker = sqlite3.connect(store.path, isolation_level=None)
    blocker.execute("PRAGMA journal_mode = WAL")
    blocker.execute("BEGIN IMMEDIATE")
    started = threading.Event()
    elapsed: dict[str, float] = {}

    def blocked_write() -> None:
        started.set()
        before = time.perf_counter()
        store.heartbeat_worker("fixture-lifecycle")
        elapsed["blocked_ms"] = (time.perf_counter() - before) * 1000

    thread = threading.Thread(target=blocked_write, daemon=True)
    thread.start()
    if not started.wait(timeout=1):
        blocker.rollback()
        blocker.close()
        raise RuntimeError("lock contention fixture did not start")
    time.sleep(LOCK_HOLD_SECONDS)
    blocker.commit()
    blocker.close()
    thread.join(timeout=2)
    if thread.is_alive():
        raise RuntimeError("lock contention fixture did not finish")
    return {
        "lock_hold_ms": LOCK_HOLD_SECONDS * 1000,
        "blocked_write_ms": elapsed["blocked_ms"],
    }
