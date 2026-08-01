"""Bounded, content-free durable runtime performance profiling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, Mapping

try:
    import resource
except ModuleNotFoundError:  # pragma: no cover - exercised on Windows
    resource = None

from gigaloom.config import HarnessConfig
from gigaloom.harnesses.echo import EchoHarness
from gigaloom.registry import HarnessRegistry
from gigaloom.runtime.api import RuntimeCoordinationStore
from gigaloom.runtime.worker import (
    DEFAULT_MAX_IDLE_SECONDS,
    DEFAULT_POLL_SECONDS,
)
from gigaloom.sessions import (
    FilesystemHarnessSessionStore,
    HarnessStoredEvent,
)
from gigaloom.sessions.contracts import utc_now
from gigaloom.types import (
    HarnessCapability,
    HarnessEventType,
)

from .runtime import (
    _OperationSample,
    _TracingRuntimeStore,
)
from .runtime_metrics import _measure


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


def _profile_request_path(root: Path) -> list[_OperationSample]:
    root.mkdir(parents=True)
    config = HarnessConfig(data_dir=root / "data")
    registry = _fixture_registry()
    store = FilesystemHarnessSessionStore(config.data_dir)
    runtime = _TracingRuntimeStore(config.data_dir)
    session = store.create_session(title="Content-free request fixture")
    run = store.create_run(
        session_id=session.id,
        harness_id="echo",
        prompt="fixture",
        model=None,
        api_mode=session.default_api_mode,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
        status="succeeded",
    )
    store.append_event(
        HarnessStoredEvent(
            id="evt-runtime-profile-terminal",
            session_id=session.id,
            run_id=run.id,
            type=HarnessEventType.RUN_FINISHED.value,
            message="Content-free fixture completed.",
            payload={"status": "succeeded"},
            created_at=utc_now(),
        )
    )

    from fastapi.testclient import TestClient

    app_holder: dict[str, Any] = {}
    startup = _measure(
        "web_app_startup",
        lambda: _create_profile_app(
            app_holder,
            config=config,
            registry=registry,
            store=store,
            runtime=runtime,
        ),
        runtime,
    )
    with TestClient(app_holder["app"]) as client:
        defaults = _measure(
            "api_defaults",
            lambda: _request_json(client, "/api/defaults"),
            runtime,
        )
        session_events = _measure(
            "api_session_events",
            lambda: _request_json(client, f"/api/sessions/{session.id}/events"),
            runtime,
        )
        sse = _measure(
            "sse_terminal_attach",
            lambda: _request_sse(client, run.id),
            runtime,
        )
        payload = client.get(f"/api/sessions/{session.id}/events").json()
        web_projection = _measure(
            "web_payload_projection",
            lambda: _web_payload_projection(payload),
        )

    return [startup, defaults, session_events, sse, web_projection]


def _create_profile_app(
    holder: dict[str, Any],
    *,
    config: HarnessConfig,
    registry: Any,
    store: FilesystemHarnessSessionStore,
    runtime: RuntimeCoordinationStore,
) -> Mapping[str, float]:
    from gigaloom.ui.app import create_app

    holder["app"] = create_app(
        config,
        registry=registry,
        store=store,
        runtime_store=runtime,
    )
    return {"apps_created": 1.0}


def _request_json(client: Any, path: str) -> Mapping[str, float]:
    response = client.get(path)
    response.raise_for_status()
    payload = response.json()
    return {
        "response_bytes": float(len(response.content)),
        "top_level_fields": float(len(payload)),
    }


def _request_sse(client: Any, run_id: str) -> Mapping[str, float]:
    with client.stream("GET", f"/api/runs/{run_id}/events/stream") as response:
        response.raise_for_status()
        text = "".join(response.iter_text())
    if '"type": "run_finished"' not in text:
        raise RuntimeError("SSE fixture did not deliver its terminal event")
    return {
        "response_bytes": float(len(text.encode("utf-8"))),
        "frames": float(text.count("data: ")),
    }


def _web_payload_projection(payload: Mapping[str, Any]) -> Mapping[str, float]:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    decoded = json.loads(encoded)
    return {
        "payload_bytes": float(len(encoded.encode("utf-8"))),
        "top_level_fields": float(len(decoded)),
    }


def _fixture_registry() -> HarnessRegistry:
    registry = HarnessRegistry()
    registry.register(EchoHarness())
    return registry
