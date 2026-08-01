"""Bounded, content-free durable runtime performance profiling."""

from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any, Final, Mapping

try:
    import resource
except ModuleNotFoundError:  # pragma: no cover - exercised on Windows
    resource = None

from gigaloom.config import HarnessConfig
from gigaloom.runtime.api import RuntimeCoordinationStore
from gigaloom.runtime.payloads import DurableJobPayloadStore
from gigaloom.runtime.worker import (
    DEFAULT_MAX_IDLE_SECONDS,
    DEFAULT_POLL_SECONDS,
    DurableJobDispatcher,
    DurableJobWorker,
)
from gigaloom.runtime.wakeup import WorkerWakeReceiver
from gigaloom.session_runner import HarnessSessionRunner
from gigaloom.sessions import FilesystemHarnessSessionStore
from gigaloom.types import (
    GigaChatApiMode,
    HarnessCapability,
)

from .runtime import (
    _CountingWorker,
    _OperationSample,
    _TracingRuntimeStore,
)
from .runtime_metrics import _measure
from .runtime_surfaces import _fixture_registry


SCHEMA_VERSION: Final[str] = "gigaloom.runtime-performance-profile.v3"
FIXTURE_SET_VERSION: Final[str] = "runtime-performance.v1"
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
    ),
    "filesystem": ("session_run_update",),
}


def _profile_session_storage(root: Path) -> list[_OperationSample]:
    store = FilesystemHarnessSessionStore(root)
    session = store.create_session(title="Content-free run update fixture")
    target = None
    for index in range(RUN_UPDATE_SCALE):
        target = store.create_run(
            session_id=session.id,
            harness_id="echo",
            prompt="fixture",
            model=None,
            api_mode=GigaChatApiMode.V2,
            capability=HarnessCapability.CHAT_COMPLETIONS,
            mode="read",
            workspace=None,
            metadata={"fixture_index": index},
        )
    if target is None:  # pragma: no cover - constant scale is positive
        raise RuntimeError("session run update fixture is empty")
    store.get_run(target.id)
    update = _measure(
        "session_run_update",
        lambda: _update_profile_run(store, target.id),
    )
    return [update]


def _update_profile_run(
    store: FilesystemHarnessSessionStore,
    run_id: str,
) -> Mapping[str, float]:
    updated = store.update_run(run_id, metadata={"profiled": True})
    if updated.id != run_id:
        raise RuntimeError("session run update fixture changed identity")
    return {
        "retained_runs": float(RUN_UPDATE_SCALE),
        "updated_runs": 1.0,
    }


def _profile_worker_resources(root: Path) -> list[_OperationSample]:
    root.mkdir(parents=True)
    registry = _fixture_registry()

    startup_root = root / "startup"
    holder: dict[str, DurableJobWorker] = {}
    startup = _measure(
        "worker_startup",
        lambda: _construct_worker(holder, startup_root, registry),
    )
    worker = holder["worker"]
    tracing = _TracingRuntimeStore(startup_root)
    worker.runtime_store = tracing
    idle_cycle = _measure(
        "worker_idle_cycle",
        lambda: {"claimed_jobs": float(worker.run_once())},
        tracing,
    )
    shutdown = _measure(
        "worker_shutdown",
        lambda: _stop_worker(worker),
        tracing,
    )

    loop_root = root / "idle-loop"
    counting = _CountingWorker(
        HarnessConfig(data_dir=loop_root),
        registry=registry,
        worker_id="fixture-idle-loop",
    )
    loop_tracing = _TracingRuntimeStore(loop_root)
    counting.runtime_store = loop_tracing
    idle_loop = _measure(
        "worker_idle_loop",
        lambda: _run_idle_loop(counting),
        loop_tracing,
    )

    wake_root = root / "wakeup"
    wake_store = _TracingRuntimeStore(wake_root)
    wake_receiver = WorkerWakeReceiver(wake_root, "fixture-wakeup")
    wake_job = wake_store.submit_job(
        session_id="fixture-session",
        user_message_id="fixture-message",
        idempotency_key="fixture-wakeup",
        initial_status="waiting_input",
    ).job
    try:
        wakeup = _measure(
            "worker_wakeup_signal",
            lambda: _measure_worker_wakeup(
                wake_receiver,
                wake_store,
                wake_job.id,
            ),
            wake_store,
        )
    finally:
        wake_receiver.close()

    active_root = root / "active"
    config = HarnessConfig(data_dir=active_root)
    sessions = FilesystemHarnessSessionStore(active_root)
    active_store = _TracingRuntimeStore(active_root)
    runner = HarnessSessionRunner(registry=registry, config=config, store=sessions)
    dispatcher = DurableJobDispatcher(
        runtime_store=active_store,
        payload_store=DurableJobPayloadStore(active_root),
        runner=runner,
    )
    session = runner.create_session(title="Content-free runtime fixture")
    submission = dispatcher.submit(
        session.id,
        {"harness_id": "echo", "prompt": "fixture", "mode": "read"},
        idempotency_key="runtime-profile-active",
    )
    active_worker = DurableJobWorker(
        config,
        registry=registry,
        worker_id="fixture-active",
    )
    active_worker.runtime_store = active_store
    active = _measure(
        "worker_active_echo",
        lambda: _run_active_worker(active_worker, active_store, submission.job.id),
        active_store,
    )
    return [startup, idle_cycle, idle_loop, wakeup, active, shutdown]


def _construct_worker(
    holder: dict[str, DurableJobWorker],
    root: Path,
    registry: Any,
) -> Mapping[str, float]:
    holder["worker"] = DurableJobWorker(
        HarnessConfig(data_dir=root),
        registry=registry,
        worker_id="fixture-startup",
    )
    return {"workers_constructed": 1.0}


def _stop_worker(worker: DurableJobWorker) -> Mapping[str, float]:
    worker.runtime_store.stop_worker(worker.worker_id)
    worker._registered = False
    return {"workers_stopped": 1.0}


def _run_idle_loop(worker: _CountingWorker) -> Mapping[str, float]:
    started = time.perf_counter()
    worker.run_forever(
        poll_seconds=IDLE_POLL_SECONDS,
        stop_on_idle_seconds=IDLE_WINDOW_SECONDS,
    )
    elapsed = max(time.perf_counter() - started, 0.000_001)
    steady_cycles = max(worker.cycles - 1, 0)
    return {
        "cycles": float(worker.cycles),
        "initial_cycles": 1.0,
        "steady_cycles_per_second": steady_cycles / elapsed,
        "projected_steady_cycles_per_minute": 60.0 / IDLE_MAX_SECONDS,
    }


def _measure_worker_wakeup(
    receiver: WorkerWakeReceiver,
    store: RuntimeCoordinationStore,
    job_id: str,
) -> Mapping[str, float]:
    outcome: list[bool] = []
    thread = threading.Thread(
        target=lambda: outcome.append(receiver.wait(MAX_WAKE_LATENCY_MS / 1000.0)),
        daemon=True,
    )
    started = time.perf_counter()
    thread.start()
    store.transition_job(job_id, "queued", expected_status="waiting_input")
    thread.join(timeout=MAX_WAKE_LATENCY_MS / 1000.0)
    latency_ms = (time.perf_counter() - started) * 1000.0
    if thread.is_alive() or outcome != [True]:
        raise RuntimeError("worker wake signal did not interrupt the idle wait")
    return {
        "delivered": 1.0,
        "latency_ms": latency_ms,
    }


def _run_active_worker(
    worker: DurableJobWorker,
    store: RuntimeCoordinationStore,
    job_id: str,
) -> Mapping[str, float]:
    claimed = worker.run_once()
    job = store.get_job(job_id)
    if not claimed or job.status.value != "succeeded":
        raise RuntimeError("active worker fixture did not complete")
    return {"completed_jobs": 1.0}
