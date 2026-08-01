"""Capture the pre-fission Settings backend cost with temporary state only."""

from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Any, Callable
from unittest.mock import patch

from fastapi.testclient import TestClient

from gigaloom.config import HarnessConfig
from gigaloom.registry import create_default_registry
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.ui.app import create_app
from gigaloom.ui.routers import settings as settings_router


SAMPLE_COUNT = 5


class _AuditCounter:
    """Count content-free filesystem reads and subprocess starts in one request."""

    def __init__(self) -> None:
        self.active = False
        self.file_reads = 0
        self.subprocesses = 0

    def hook(self, event: str, args: tuple[Any, ...]) -> None:
        if not self.active:
            return
        if event == "open":
            mode = args[1] if len(args) > 1 else None
            if isinstance(mode, str) and "r" in mode:
                self.file_reads += 1
            elif isinstance(mode, int) and mode == 0:
                self.file_reads += 1
        elif event == "subprocess.Popen":
            self.subprocesses += 1


class _Recorder:
    """Collect component wall time and call counts without recording arguments."""

    def __init__(self) -> None:
        self.elapsed_ms: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)

    def wrap(self, name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        def measured(*args: Any, **kwargs: Any) -> Any:
            started = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.elapsed_ms[name] += (perf_counter() - started) * 1_000
                self.calls[name] += 1

        return measured


class _HarnessProxy:
    def __init__(self, harness: Any, recorder: _Recorder) -> None:
        self._harness = harness
        self._recorder = recorder

    def spec(self) -> Any:
        return self._recorder.wrap("harness.spec", self._harness.spec)()

    def availability(self) -> Any:
        return self._recorder.wrap(
            "harness.availability",
            self._harness.availability,
        )()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._harness, name)


class _RegistryProxy:
    def __init__(self, registry: Any, recorder: _Recorder) -> None:
        self._registry = registry
        self._recorder = recorder
        self._proxies = {
            harness.spec().id: _HarnessProxy(harness, recorder)
            for harness in registry.list()
        }

    def list(self) -> tuple[Any, ...]:
        started = perf_counter()
        try:
            return tuple(self._proxies[key] for key in sorted(self._proxies))
        finally:
            self._recorder.elapsed_ms["harness_registry.list"] += (
                perf_counter() - started
            ) * 1_000
            self._recorder.calls["harness_registry.list"] += 1

    def get(self, harness_id: str) -> Any:
        return self._proxies[harness_id]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)


def _request(
    client: TestClient,
    workspace: Path,
    audit: _AuditCounter,
    recorder: _Recorder,
) -> dict[str, Any]:
    recorder.elapsed_ms.clear()
    recorder.calls.clear()
    audit.file_reads = 0
    audit.subprocesses = 0
    started = perf_counter()
    audit.active = True
    try:
        response = client.get("/api/settings", params={"workspace": str(workspace)})
    finally:
        audit.active = False
    elapsed_ms = (perf_counter() - started) * 1_000
    response.raise_for_status()
    body = response.json()
    return {
        "wall_ms": round(elapsed_ms, 3),
        "response_bytes": len(response.content),
        "filesystem_reads": audit.file_reads,
        "subprocesses_started": audit.subprocesses,
        "native_or_provider_probe_calls": recorder.calls.get(
            "harness.availability",
            0,
        ),
        "component_ms": {
            key: round(value, 3) for key, value in sorted(recorder.elapsed_ms.items())
        },
        "component_calls": dict(sorted(recorder.calls.items())),
        "bounded_counts": {
            "harnesses": len(body["harness_defaults"]["harnesses"]),
            "mcp_servers": len(body["mcp"]["servers"]),
            "providers": body["provider"]["count"],
        },
    }


def _sample(audit: _AuditCounter) -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="gigaloom-settings-baseline-") as root:
        root_path = Path(root)
        workspace = root_path / "workspace"
        workspace.mkdir()
        recorder = _Recorder()
        registry = _RegistryProxy(
            create_default_registry(include_entry_points=False),
            recorder,
        )
        app = create_app(
            HarnessConfig(data_dir=str(root_path / "state")),
            registry=registry,
            store=InMemoryHarnessSessionStore(),
        )
        with ExitStack() as stack:
            for name in (
                "resolve_project",
                "load_project_config",
                "load_project_state",
                "build_mcp_inventory",
            ):
                original = getattr(settings_router, name)
                stack.enter_context(
                    patch.object(
                        settings_router,
                        name,
                        recorder.wrap(name, original),
                    )
                )
            stack.enter_context(
                patch.object(
                    app.state.harness_settings_store,
                    "load",
                    recorder.wrap(
                        "settings_store.load",
                        app.state.harness_settings_store.load,
                    ),
                )
            )
            stack.enter_context(
                patch.object(
                    app.state.harness_provider_settings_service,
                    "list",
                    recorder.wrap(
                        "provider_registry.list",
                        app.state.harness_provider_settings_service.list,
                    ),
                )
            )
            stack.enter_context(
                patch.object(
                    app.state.harness_async_diagnostics,
                    "snapshot",
                    recorder.wrap(
                        "diagnostics.snapshot",
                        app.state.harness_async_diagnostics.snapshot,
                    ),
                )
            )
            with TestClient(app) as client:
                cold = _request(client, workspace, audit, recorder)
                warm = _request(client, workspace, audit, recorder)
        return cold, warm


def _percentiles(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(_percentile(ordered, 0.95), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _percentile(ordered: list[float], quantile: float) -> float:
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def main() -> int:
    """Print one content-free baseline document to stdout."""
    audit = _AuditCounter()
    sys.addaudithook(audit.hook)
    samples = [_sample(audit) for _ in range(SAMPLE_COUNT)]
    cold = [sample[0] for sample in samples]
    warm = [sample[1] for sample in samples]
    source_commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = {
        "schema_version": "gigaloom.settings-initial-load-backend.v1",
        "source_commit": source_commit,
        "environment": {
            "implementation": platform.python_implementation(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "system": platform.system(),
        },
        "privacy": {
            "content_free": True,
            "external_network_accessed": False,
            "native_homes_accessed": False,
            "temporary_state_only": True,
        },
        "cold": {
            "wall": _percentiles([item["wall_ms"] for item in cold]),
            "samples": cold,
        },
        "warm": {
            "wall": _percentiles([item["wall_ms"] for item in warm]),
            "samples": warm,
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
