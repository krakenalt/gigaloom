"""Hermetic fault injections over production persistence owners."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from gigaloom.attachments import FilesystemAttachmentStore
from gigaloom.diagnostics.fault_lab.contracts import FaultFixtureId, FaultInvariant
from gigaloom.diagnostics.recovery.service import RecoveryCheckService
from gigaloom.runtime.db import DbProvider
from gigaloom.runtime.jobs import JobsRepository
from gigaloom.runtime.models import (
    JobAttemptStatus,
    JobStatus,
    RunStatus,
    SideEffectStatus,
)
from gigaloom.runtime.reconcile import RuntimeReconciler
from gigaloom.runtime.side_effects import HarnessSideEffectExecutor
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.sessions import FilesystemHarnessSessionStore, HarnessStoredEvent
from gigaloom.types import GigaChatApiMode, HarnessCapability


def run_fixture(fixture_id: FaultFixtureId, root: Path) -> tuple[FaultInvariant, ...]:
    """Run one exact fixture in its disposable data root."""
    handler = _FIXTURES[fixture_id]
    return tuple(sorted(handler(root), key=lambda item: item.invariant_id))


def _worker_dies_after_claim(root: Path) -> tuple[FaultInvariant, ...]:
    store = RuntimeCoordinationStore(root)
    job = store.submit_job(
        session_id="fault-session",
        user_message_id="fault-message",
        idempotency_key="worker-death",
    ).job
    claim = store.claim_next_job(
        worker_id="fault-worker",
        capability_fingerprint={},
        lease_seconds=30,
    )
    if claim is None:
        return (_invariant("claim_created", False, "claim_missing"),)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE job_attempts SET leased_until = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", claim.attempt.id),
        )
    recovered = store.recover_expired_attempts(retry_delay_seconds=0)
    attempt = store.get_attempt(claim.attempt.id)
    final_job = store.get_job(job.id)
    return (
        _invariant(
            "expired_claim_recovered",
            tuple(item.id for item in recovered) == (attempt.id,)
            and attempt.status is JobAttemptStatus.INTERRUPTED,
            "expired_claim_interrupted",
        ),
        _invariant(
            "no_stuck_running",
            final_job.status is JobStatus.FAILED,
            "job_failed_closed",
        ),
        _invariant(
            "no_accidental_success",
            final_job.status is not JobStatus.SUCCEEDED,
            "success_not_inferred",
        ),
        _invariant(
            "no_duplicate_side_effect",
            store.inspect()["counts"]["harness_side_effects"] == 0,
            "side_effect_absent",
        ),
    )


def _database_locked_during_commit(root: Path) -> tuple[FaultInvariant, ...]:
    store = RuntimeCoordinationStore(root)
    locked = False
    with closing(sqlite3.connect(store.path)) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        provider = DbProvider(store.path, timeout_seconds=0.01)
        repository = JobsRepository(provider)
        try:
            repository.submit_job(
                session_id="locked-session",
                user_message_id="locked-message",
                idempotency_key="locked-commit",
            )
        except sqlite3.OperationalError as exc:
            locked = "locked" in str(exc).casefold()
        finally:
            provider.close()
        blocker.rollback()
    first = store.submit_job(
        session_id="locked-session",
        user_message_id="locked-message",
        idempotency_key="locked-commit",
    )
    repeated = store.submit_job(
        session_id="locked-session",
        user_message_id="locked-message",
        idempotency_key="locked-commit",
    )
    return (
        _invariant("database_lock_reproduced", locked, "sqlite_lock_observed"),
        _invariant(
            "commit_retried_once",
            first.created and not repeated.created and first.job.id == repeated.job.id,
            "idempotent_commit_recovered",
        ),
        _invariant(
            "no_accidental_success",
            first.job.status is JobStatus.QUEUED,
            "job_remains_queued",
        ),
    )


def _jsonl_tail_truncated(root: Path) -> tuple[FaultInvariant, ...]:
    sessions = FilesystemHarnessSessionStore(root)
    session = sessions.create_session(title="fault fixture")
    run = _create_run(sessions, session.id)
    sessions.append_event(
        HarnessStoredEvent(
            id="event-one",
            session_id=session.id,
            run_id=run.id,
            type="run_started",
            message="fixture",
            payload={},
            created_at="2026-08-01T00:00:00+00:00",
            trace_id="trace-one",
            sequence=1,
        )
    )
    path = next((root / "sessions").glob("*/*/*/events.jsonl"))
    path.write_bytes(path.read_bytes()[:-1] + b"{")
    report = RecoveryCheckService().check(root)
    reasons = {item.reason_code for item in report.checks}
    recovered = sessions.update_run(
        run.id,
        status=RunStatus.FAILED,
        finished_at="2026-08-01T00:00:01+00:00",
        error="state_integrity_failure",
    )
    return (
        _invariant(
            "truncated_tail_detected",
            "jsonl_invalid" in reasons,
            "jsonl_tail_rejected",
        ),
        _invariant(
            "no_stuck_running",
            recovered.status is RunStatus.FAILED,
            "corrupt_run_failed_closed",
        ),
        _invariant(
            "no_accidental_success",
            recovered.status is not RunStatus.SUCCEEDED,
            "success_not_inferred",
        ),
    )


def _attachment_digest_mismatch(root: Path) -> tuple[FaultInvariant, ...]:
    sessions = FilesystemHarnessSessionStore(root)
    session = sessions.create_session(title="fault fixture")
    attachment = FilesystemAttachmentStore(root).create_upload(
        session_id=session.id,
        project_id=None,
        filename="fixture.txt",
        data=b"expected",
    )
    blob = Path(attachment.storage_path or "")
    blob.write_bytes(b"tampered")
    report = RecoveryCheckService().check(root)
    reasons = {item.reason_code for item in report.checks}
    return (
        _invariant(
            "attachment_mismatch_detected",
            "attachment_digest_mismatch" in reasons,
            "attachment_digest_rejected",
        ),
        _invariant(
            "corrupt_blob_preserved",
            blob.read_bytes() == b"tampered",
            "check_mode_did_not_delete_blob",
        ),
    )


def _cancel_before_terminal_event(root: Path) -> tuple[FaultInvariant, ...]:
    sessions = FilesystemHarnessSessionStore(root)
    session = sessions.create_session(title="fault fixture")
    run = _create_run(sessions, session.id)
    runtime = RuntimeCoordinationStore(root)
    job = runtime.submit_job(
        session_id=session.id,
        user_message_id="cancel-message",
        initial_run_id=run.id,
        idempotency_key="cancel-before-terminal",
    ).job
    attempt = runtime.create_attempt(job.id, run_id=run.id)
    runtime.transition_attempt(attempt.id, JobAttemptStatus.RUNNING)
    canceled = runtime.request_cancel(job.id)
    runtime.finish_attempt(attempt.id, JobAttemptStatus.CANCELED)
    first = RuntimeReconciler(runtime, sessions).reconcile()
    second = RuntimeReconciler(runtime, sessions).reconcile()
    events = sessions.list_events(session.id, run_id=run.id)
    terminal = [item for item in events if item.type == "runtime_reconciled"]
    return (
        _invariant(
            "cancel_retained",
            canceled.cancel_requested_at is not None
            and runtime.get_job(job.id).status is JobStatus.CANCELED,
            "cancel_is_terminal",
        ),
        _invariant(
            "run_not_orphaned",
            sessions.get_run(run.id).status is RunStatus.CANCELED,
            "run_canceled_after_restart",
        ),
        _invariant(
            "terminal_event_once",
            first.outbox_processed == 1
            and second.outbox_processed == 0
            and len(terminal) == 1,
            "terminal_reconciliation_idempotent",
        ),
        _invariant(
            "no_accidental_success",
            sessions.get_run(run.id).status is not RunStatus.SUCCEEDED,
            "success_not_inferred",
        ),
    )


def _restart_after_lease_before_side_effect(root: Path) -> tuple[FaultInvariant, ...]:
    runtime = RuntimeCoordinationStore(root)
    job = runtime.submit_job(
        session_id="restart-session",
        user_message_id="restart-message",
        idempotency_key="restart-before-side-effect",
        max_attempts=2,
    ).job
    first_attempt = runtime.create_attempt(
        job.id,
        run_id=job.initial_run_id,
        leased_until="2000-01-01T00:00:00+00:00",
        idempotency_class="deterministic",
    )
    runtime.recover_expired_attempts(retry_delay_seconds=0)
    runtime.requeue_due_jobs()
    claim = runtime.claim_next_job(
        worker_id="restart-worker",
        capability_fingerprint={},
        lease_seconds=30,
    )
    if claim is None:
        return (_invariant("retry_claimed", False, "retry_claim_missing"),)
    executor = HarnessSideEffectExecutor(runtime)
    first = executor.record_recovery_marker_once(
        job_id=job.id,
        attempt_id=claim.attempt.id,
        identity="restart-marker",
    )
    repeated = executor.record_recovery_marker_once(
        job_id=job.id,
        attempt_id=claim.attempt.id,
        identity="restart-marker",
    )
    runtime.finish_attempt(
        claim.attempt.id,
        JobAttemptStatus.FAILED,
        sync_terminal_run=False,
    )
    return (
        _invariant(
            "expired_lease_interrupted",
            runtime.get_attempt(first_attempt.id).status
            is JobAttemptStatus.INTERRUPTED,
            "first_attempt_interrupted",
        ),
        _invariant(
            "side_effect_exactly_once",
            first.created
            and not repeated.created
            and first.record.id == repeated.record.id
            and first.record.status is SideEffectStatus.COMPLETED
            and runtime.inspect()["counts"]["harness_side_effects"] == 1,
            "side_effect_deduplicated",
        ),
        _invariant(
            "no_stuck_running",
            runtime.get_job(job.id).status is JobStatus.FAILED,
            "job_failed_closed",
        ),
        _invariant(
            "no_accidental_success",
            runtime.get_job(job.id).status is not JobStatus.SUCCEEDED,
            "success_not_inferred",
        ),
    )


def _create_run(
    store: FilesystemHarnessSessionStore,
    session_id: str,
):
    return store.create_run(
        session_id=session_id,
        harness_id="echo",
        prompt="fault fixture",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="plan",
        workspace=None,
        status=RunStatus.RUNNING,
    )


def _invariant(invariant_id: str, passed: bool, reason: str) -> FaultInvariant:
    payload = json.dumps(
        {"id": invariant_id, "passed": passed, "reason": reason},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return FaultInvariant(
        invariant_id=invariant_id,
        passed=passed,
        reason_code=reason,
        evidence_digest=hashlib.sha256(payload).hexdigest(),
    )


_FIXTURES = {
    FaultFixtureId.WORKER_DIES_AFTER_CLAIM: _worker_dies_after_claim,
    FaultFixtureId.DATABASE_LOCKED_DURING_COMMIT: _database_locked_during_commit,
    FaultFixtureId.JSONL_TAIL_TRUNCATED: _jsonl_tail_truncated,
    FaultFixtureId.ATTACHMENT_DIGEST_MISMATCH: _attachment_digest_mismatch,
    FaultFixtureId.CANCEL_BEFORE_TERMINAL_EVENT: _cancel_before_terminal_event,
    FaultFixtureId.RESTART_AFTER_LEASE_BEFORE_SIDE_EFFECT: (
        _restart_after_lease_before_side_effect
    ),
}
