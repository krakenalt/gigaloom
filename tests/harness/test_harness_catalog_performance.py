"""Structural performance checks for the Harness catalog projection."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gigaloom.cli_capabilities import invalidate_cli_probe_cache
from gigaloom.config import HarnessConfig
from gigaloom.executables import ExecutableResolution
from gigaloom.harnesses.base import BaseHarness
from gigaloom.registry import HarnessRegistry
from gigaloom.types import (
    Availability,
    HarnessCapability,
    HarnessResult,
    HarnessSpec,
)
from gigaloom.ui.app import create_app


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    invalidate_cli_probe_cache()
    yield
    invalidate_cli_probe_cache()


def test_catalog_probes_each_external_cli_once(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_run(command, **_kwargs):  # noqa: ANN001, ANN202
        calls.append(tuple(command))
        name = Path(command[0]).name
        if command[-1] == "--version":
            output = {
                "codex": "codex-cli 0.146.0",
                "claude": "2.1.197 (Claude Code)",
                "gemini": "0.46.0",
            }[name]
        elif "app-server" in command:
            output = "--listen stdio:// generate-json-schema"
        elif "remote-control" in command:
            output = "remote control"
        elif name == "codex":
            output = "exec --json --sandbox --ephemeral --image --config"
        elif name == "claude":
            output = (
                "--output-format stream-json --permission-mode "
                "--no-session-persistence --remote-control"
            )
        else:
            output = "--output-format stream-json --approval-mode --skip-trust --acp"
        return SimpleNamespace(stdout=output, stderr="", returncode=0)

    monkeypatch.setattr("gigaloom.cli_capabilities.subprocess.run", fake_run)
    registry = HarnessRegistry.with_builtins(executable_resolver=_FixtureResolver())
    client = TestClient(
        create_app(HarnessConfig(data_dir=str(tmp_path)), registry=registry)
    )

    response = client.get("/api/harnesses")

    assert response.status_code == 200
    by_executable = Counter(Path(call[0]).name for call in calls)
    version_calls = Counter(
        Path(call[0]).name for call in calls if call[-1] == "--version"
    )
    assert by_executable == {"codex": 3, "claude": 3, "gemini": 2}
    assert version_calls == {"codex": 1, "claude": 1, "gemini": 1}


def test_catalog_snapshots_dynamic_discovery_and_availability(tmp_path):
    registry = HarnessRegistry()
    app = create_app(HarnessConfig(data_dir=str(tmp_path)), registry=registry)
    availability_calls: Counter[str] = Counter()
    provider_calls = 0
    harnesses = tuple(
        _DynamicHarness(harness_id, availability_calls)
        for harness_id in ("dynamic-a", "dynamic-b")
    )

    def provider() -> tuple[BaseHarness, ...]:
        nonlocal provider_calls
        provider_calls += 1
        return harnesses

    registry._dynamic_provider = provider  # noqa: SLF001

    response = TestClient(app).get("/api/harnesses")

    assert response.status_code == 200
    assert provider_calls == 1
    assert availability_calls == {"dynamic-a": 1, "dynamic-b": 1}


class _FixtureResolver:
    def resolve(self, harness_id: str, command_name: str) -> ExecutableResolution:
        executable = f"/fixture/{command_name}"
        return ExecutableResolution(
            harness_id=harness_id,
            command_name=command_name,
            executable=executable,
            source="fixture",
            argv=(executable,),
        )


class _DynamicHarness(BaseHarness):
    def __init__(self, harness_id: str, calls: Counter[str]) -> None:
        self._harness_id = harness_id
        self._calls = calls

    def spec(self) -> HarnessSpec:
        return HarnessSpec(
            id=self._harness_id,
            title=self._harness_id,
            kind="agent-cli",
            description="Dynamic performance fixture",
            capabilities=(HarnessCapability.AGENT_CLI,),
            supports_workspace=True,
            tags=("agent",),
        )

    def availability(self) -> Availability:
        self._calls[self._harness_id] += 1
        return Availability.available("fixture")

    def run(self, request, context) -> HarnessResult:  # noqa: ANN001
        return HarnessResult(ok=True, text="fixture")
