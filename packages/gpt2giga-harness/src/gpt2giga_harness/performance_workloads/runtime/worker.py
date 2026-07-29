"""Worker lifecycle and maintenance workload."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec
from gpt2giga_harness.performance_workloads.runtime.contracts import (
    RuntimeCase,
    RuntimeCaseFactory,
)
from gpt2giga_harness.performance_workloads.runtime.instrumentation import (
    RuntimeCounters,
    TracingRuntimeStore,
)
from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.harnesses.echo import EchoHarness
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.runtime.reconcile import RuntimeReconciler
from gpt2giga_harness.runtime.worker import DurableJobWorker
from gpt2giga_harness.sessions import FilesystemHarnessSessionStore


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="runtime.worker.lifecycle",
        family="runtime/worker",
        profiles=("runtime-detail",),
        variants=(
            "heartbeat",
            "idle",
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
        future_gate="G-PERF",
    ),
)


def case_factories() -> tuple[RuntimeCaseFactory, ...]:
    """Return heartbeat, idle, and maintenance cadence cases."""
    return (
        _heartbeat_factory,
        _idle_factory,
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

    def operation(counters: RuntimeCounters) -> dict[str, int]:
        store.heartbeat_worker("fixture-worker")
        return {"heartbeats": 1}

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
