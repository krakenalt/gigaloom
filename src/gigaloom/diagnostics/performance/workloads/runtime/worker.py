"""Worker lifecycle and maintenance workload."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from gigaloom.diagnostics.performance.workloads import WorkloadSpec
from gigaloom.diagnostics.performance.workloads.runtime.contracts import (
    RuntimeCase,
    RuntimeCaseFactory,
)
from gigaloom.diagnostics.performance.workloads.runtime.instrumentation import (
    RuntimeCounters,
    TracingRuntimeStore,
)
from gigaloom.config import HarnessConfig
from gigaloom.harnesses.echo import EchoHarness
from gigaloom.registry import HarnessRegistry
from gigaloom.runtime.reconcile import RuntimeReconciler
from gigaloom.runtime.worker import DurableJobWorker
from gigaloom.runtime.workers.scheduler import (
    MAINTENANCE_TASKS,
    WorkerMaintenanceRunner,
    WorkerMaintenanceScheduler,
)
from gigaloom.sessions import FilesystemHarnessSessionStore


IDLE_MINUTE_CYCLES: Final[int] = 60


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="runtime.worker.lifecycle",
        family="runtime/worker",
        profiles=("runtime-detail",),
        variants=(
            "heartbeat",
            "idle",
            "idle_minute",
            "schedule",
            "recovery",
            "reconcile",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "wakeups",
            "sqlite_connections",
            "sqlite_statements",
            "maintenance_cycles",
        ),
        future_gate="performance-regression-followup",
    ),
)


def case_factories() -> tuple[RuntimeCaseFactory, ...]:
    """Return heartbeat, idle, and maintenance cadence cases."""
    return (
        _heartbeat_factory,
        _idle_factory,
        _idle_minute_factory,
        _schedule_factory,
        _recovery_factory,
        _reconcile_factory,
    )


def _heartbeat_factory(root: Path) -> RuntimeCase:
    store = TracingRuntimeStore(root)
    store.register_worker(
        worker_id="fixture-worker",
        process_id=os.getpid(),
        hostname="fixture",
        capability_fingerprint={},
    )
    store.submit_job(
        session_id="fixture-session",
        user_message_id="fixture-message",
        initial_run_id="fixture-run",
        idempotency_key="fixture-heartbeat",
    )
    claim = store.claim_next_job(
        worker_id="fixture-worker",
        capability_fingerprint={},
        lease_seconds=5,
    )
    if claim is None:
        raise RuntimeError("heartbeat fixture could not claim its prepared job")

    def operation(counters: RuntimeCounters) -> dict[str, int]:
        store.heartbeat_worker_attempt(
            claim.attempt.id,
            worker_id="fixture-worker",
            lease_seconds=5,
        )
        return {"heartbeats": 1, "attempt_leases": 1}

    return _case("heartbeat", store, operation)


def _idle_factory(root: Path) -> RuntimeCase:
    worker, store = _prepared_worker(root, worker_id="fixture-idle")

    def operation(counters: RuntimeCounters) -> dict[str, int]:
        claimed = worker.run_once()
        counters.maintenance_cycles = 1
        counters.claimed_jobs = int(claimed)
        if claimed:
            raise RuntimeError("idle worker fixture unexpectedly claimed work")
        return {"idle_cycles": 1, "claimed_jobs": 0}

    return _case("idle", store, operation)


def _idle_minute_factory(root: Path) -> RuntimeCase:
    baseline_worker, baseline_store = _prepared_worker(
        root / "baseline", worker_id="fixture-idle-baseline"
    )
    baseline_before = baseline_store.trace_snapshot()
    for _ in range(IDLE_MINUTE_CYCLES):
        _legacy_idle_cycle(baseline_worker, baseline_store)
    baseline_delta = baseline_store.trace_snapshot() - baseline_before

    now = [0.0]
    worker, store = _prepared_worker(root / "current", worker_id="fixture-idle-minute")
    worker._maintenance = WorkerMaintenanceScheduler(
        heartbeat_seconds=worker.heartbeat_seconds,
        clock=lambda: now[0],
    )
    worker._maintenance_runner = WorkerMaintenanceRunner(
        scheduler=worker._maintenance,
        runtime_store=store,
        session_store=worker.session_store,
        worker_id=worker.worker_id,
        trigger_schedules=lambda: worker._trigger_schedules(),
    )

    def operation(counters: RuntimeCounters) -> dict[str, int | float]:
        before = store.trace_snapshot()
        for second in range(IDLE_MINUTE_CYCLES):
            now[0] = float(second)
            counters.maintenance_cycles += sum(
                int(worker._maintenance.is_due(task)) for task in MAINTENANCE_TASKS
            )
            claimed = worker.run_once()
            counters.claimed_jobs += int(claimed)
            store.next_worker_maintenance_delay(1.0)
            if claimed:
                raise RuntimeError("idle minute fixture unexpectedly claimed work")
        current_delta = store.trace_snapshot() - before
        return {
            "cycles": IDLE_MINUTE_CYCLES,
            "baseline_maintenance_cycles": (
                IDLE_MINUTE_CYCLES * len(MAINTENANCE_TASKS)
            ),
            "baseline_sql_connections": baseline_delta.connections,
            "baseline_sql_statements": (
                baseline_delta.reads + baseline_delta.writes + baseline_delta.schema
            ),
            "current_sql_connections": current_delta.connections,
            "current_sql_statements": (
                current_delta.reads + current_delta.writes + current_delta.schema
            ),
        }

    return RuntimeCase(
        id="runtime.worker.lifecycle.idle_minute",
        family="runtime/worker",
        fixture={
            "temporary_state": True,
            "idle_seconds": IDLE_MINUTE_CYCLES,
            "legacy_cadence_measured": True,
        },
        store=store,
        operation=operation,
    )


def _schedule_factory(root: Path) -> RuntimeCase:
    worker, store = _prepared_worker(root, worker_id="fixture-schedule")

    def operation(counters: RuntimeCounters) -> dict[str, int]:
        worker._trigger_schedules()
        counters.maintenance_cycles = 1
        return {"schedule_cycles": 1, "due_occurrences": 0}

    return _case("schedule", store, operation)


def _recovery_factory(root: Path) -> RuntimeCase:
    store = TracingRuntimeStore(root)

    def operation(counters: RuntimeCounters) -> dict[str, int]:
        recovered = store.recover_expired_attempts(retry_delay_seconds=0)
        counters.maintenance_cycles = 1
        counters.rows_parsed = len(recovered)
        return {"recovery_cycles": 1, "attempts_recovered": len(recovered)}

    return _case("recovery", store, operation)


def _reconcile_factory(root: Path) -> RuntimeCase:
    store = TracingRuntimeStore(root)
    sessions = FilesystemHarnessSessionStore(root)

    def operation(counters: RuntimeCounters) -> dict[str, int]:
        report = RuntimeReconciler(store, sessions).reconcile()
        counters.maintenance_cycles = 1
        counters.rows_parsed = report.runs_scanned
        if report.outbox_failed:
            raise RuntimeError("reconcile cadence fixture failed")
        return {
            "reconcile_cycles": 1,
            "runs_scanned": report.runs_scanned,
            "outbox_processed": report.outbox_processed,
        }

    return _case("reconcile", store, operation)


def _legacy_idle_cycle(
    worker: DurableJobWorker,
    store: TracingRuntimeStore,
) -> None:
    store.heartbeat_worker(worker.worker_id)
    worker._trigger_schedules()
    store.recover_expired_attempts()
    store.requeue_due_jobs()
    RuntimeReconciler(store, worker.session_store).reconcile()
    store.claim_next_job(
        worker_id=worker.worker_id,
        capability_fingerprint=worker.fingerprint,
        lease_seconds=worker.lease_seconds,
    )
    store.next_worker_maintenance_delay(1.0)


def _prepared_worker(
    root: Path,
    *,
    worker_id: str,
) -> tuple[DurableJobWorker, TracingRuntimeStore]:
    registry = HarnessRegistry()
    registry.register(EchoHarness())
    worker = DurableJobWorker(
        HarnessConfig(data_dir=root),
        registry=registry,
        worker_id=worker_id,
    )
    store = TracingRuntimeStore(root)
    worker.runtime_store = store
    worker._register()
    return worker, store


def _case(
    variant: str,
    store: TracingRuntimeStore,
    operation,
) -> RuntimeCase:
    return RuntimeCase(
        id=f"runtime.worker.lifecycle.{variant}",
        family="runtime/worker",
        fixture={"temporary_state": True, "maintenance_cycles": 1},
        store=store,
        operation=operation,
    )
