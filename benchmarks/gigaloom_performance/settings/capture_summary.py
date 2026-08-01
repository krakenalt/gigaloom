"""Capture current cold and warm Settings summary performance evidence."""

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
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from gigaloom.config import HarnessConfig
from gigaloom.registry import create_default_registry
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.ui.app import create_app


SAMPLE_COUNT = 5


class _AuditCounter:
    """Count content-free filesystem reads and subprocess starts per request."""

    def __init__(self) -> None:
        self.active = False
        self.file_reads = 0
        self.subprocesses = 0

    def hook(self, event: str, args: tuple[Any, ...]) -> None:
        if not self.active:
            return
        if event == "open":
            mode = args[1] if len(args) > 1 else None
            if (isinstance(mode, str) and "r" in mode) or mode == 0:
                self.file_reads += 1
        elif event == "subprocess.Popen":
            self.subprocesses += 1


class _ProbeCounter:
    """Fail the capture if an initial read crosses a process-owned boundary."""

    def __init__(self) -> None:
        self.calls = 0

    def reset(self) -> None:
        self.calls = 0

    def forbidden(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Settings summary crossed a process-owned boundary")


def _request(
    client: TestClient,
    workspace: Path,
    audit: _AuditCounter,
    probes: _ProbeCounter,
) -> dict[str, Any]:
    audit.file_reads = 0
    audit.subprocesses = 0
    probes.reset()
    started = perf_counter()
    audit.active = True
    try:
        response = client.get(
            "/api/settings/summary",
            params={"workspace": str(workspace)},
        )
    finally:
        audit.active = False
    elapsed_ms = (perf_counter() - started) * 1_000
    response.raise_for_status()
    body = response.json()
    return {
        "filesystem_reads": audit.file_reads,
        "native_or_provider_probe_calls": probes.calls,
        "response_bytes": len(response.content),
        "section_revisions": len(body["sections"]) + 2,
        "subprocesses_started": audit.subprocesses,
        "wall_ms": round(elapsed_ms, 3),
    }


def _sample(audit: _AuditCounter) -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="gigaloom-settings-summary-") as root:
        root_path = Path(root)
        workspace = root_path / "workspace"
        workspace.mkdir()
        registry = create_default_registry(include_entry_points=False)
        probes = _ProbeCounter()
        app = create_app(
            HarnessConfig(data_dir=str(root_path / "state")),
            registry=registry,
            store=InMemoryHarnessSessionStore(),
        )
        with ExitStack() as stack:
            for harness in registry.list():
                for method in (
                    "availability",
                    "capability_probe",
                    "durable_structured_capabilities",
                    "executable_resolution",
                ):
                    if hasattr(harness, method):
                        stack.enter_context(
                            patch.object(harness, method, probes.forbidden)
                        )
            stack.enter_context(
                patch.object(
                    app.state.harness_native_login_broker,
                    "list_accounts",
                    probes.forbidden,
                )
            )
            with TestClient(app) as client:
                cold = _request(client, workspace, audit, probes)
                warm = _request(client, workspace, audit, probes)
        return cold, warm


def _summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    wall = sorted(float(sample["wall_ms"]) for sample in samples)
    counts: dict[str, dict[str, int]] = {}
    for name in (
        "filesystem_reads",
        "native_or_provider_probe_calls",
        "subprocesses_started",
    ):
        values = sorted(int(sample[name]) for sample in samples)
        counts[name] = {
            "maximum": values[-1],
            "minimum": values[0],
            "p50": int(statistics.median(values)),
        }
    return {
        "operation_counts": counts,
        "response_bytes": {
            "maximum": max(int(sample["response_bytes"]) for sample in samples),
            "minimum": min(int(sample["response_bytes"]) for sample in samples),
        },
        "samples": samples,
        "wall": {
            "max_ms": round(wall[-1], 3),
            "min_ms": round(wall[0], 3),
            "p50_ms": round(statistics.median(wall), 3),
            "p95_ms": round(_percentile(wall, 0.95), 3),
            "samples": len(wall),
        },
    }


def _percentile(ordered: list[float], quantile: float) -> float:
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def main() -> int:
    """Print one content-free current-summary evidence document."""
    audit = _AuditCounter()
    sys.addaudithook(audit.hook)
    samples = [_sample(audit) for _ in range(SAMPLE_COUNT)]
    phases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cold, warm in samples:
        phases["cold"].append(cold)
        phases["warm"].append(warm)
    source_commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload = {
        "environment": {
            "implementation": platform.python_implementation(),
            "machine": platform.machine(),
            "platform": platform.system(),
            "python": platform.python_version(),
        },
        "privacy": {
            "content_free": True,
            "external_network_accessed": False,
            "native_homes_accessed": False,
            "provider_traffic": False,
            "temporary_state_only": True,
        },
        "schema_version": "gigaloom.settings-summary.v1",
        "source_commit": source_commit,
        "workloads": {
            "settings.summary.cold": _summary(phases["cold"]),
            "settings.summary.warm": _summary(phases["warm"]),
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
