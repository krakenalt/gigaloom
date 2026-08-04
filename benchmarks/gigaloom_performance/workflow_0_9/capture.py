"""Capture comparable GigaLoom 0.9 workflow p50/p95 evidence."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import tempfile
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from gigaloom.application.product_evidence import ProductEvidenceApplication
from gigaloom.attachments import decode_attachment_text
from gigaloom.config import HarnessConfig
from gigaloom.contracts.product_evidence_codec import product_evidence_report_to_dict
from gigaloom.execution.thread_relay import (
    GigaLoomThreadRelayActions,
    ThreadRelayScopeV1,
)
from gigaloom.harnesses import EchoHarness
from gigaloom.native.api import (
    BridgeRouteV1,
    GatewayArtifactEvidenceV1,
    GatewayDiscoveryResult,
    GatewayDiscoveryStatus,
    GatewayMode,
    GatewayProfileV1,
    GatewayRouteCatalogV1,
    GatewayRouteDiscovery,
    GatewaySidecarStatus,
    GatewaySupportStatus,
    ManagedGatewaySidecarService,
)
from gigaloom.native.base import NativeCommandPlan
from gigaloom.native.process import NativeProcessRef, NativeProcessStatus
from gigaloom.projects.api import instructions_api
from gigaloom.registry import HarnessRegistry
from gigaloom.sessions import (
    FilesystemHarnessSessionStore,
    HarnessMessage,
    HarnessStoredEvent,
    InMemoryHarnessSessionStore,
    ThreadDeliveryRepository,
)
from gigaloom.sessions.contracts import utc_now
from gigaloom.types import GigaChatApiMode, HarnessCapability
from gigaloom.ui.app import create_app
from gigaloom.ui.services.gateway_routes import GatewayRouteWebService


SCHEMA_VERSION = "gigaloom.workflow-0.9-performance.v1"
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
WORK_INITIAL_PATHS = (
    "/api/cockpit/sessions?limit=50",
    "/api/runs?limit=25",
    "/api/harnesses",
    "/api/models?api_mode=v2",
    "/api/settings",
    "/api/gateway/routes",
)


@dataclass
class _Case:
    id: str
    fixture: Mapping[str, Any]
    operation: Callable[[], object]
    details: Callable[[object], Mapping[str, float]]
    before_each: Callable[[], None] = lambda: None


class _NoopSubmitter:
    def submit_turn(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("performance read fixture attempted a turn mutation")


class _StaticGatewayCatalog:
    gateway_api_key = None

    def close(self) -> None:
        """Match the application-owned gateway service lifecycle."""

    def catalog(self, *, refresh: bool = False) -> dict[str, object]:
        del refresh
        return {
            "status": "unknown",
            "reason_ids": ["fixture_no_live_gateway"],
            "routes": [],
        }

    def preflight(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("initial Work load attempted gateway preflight")


class _StaticDiscovery:
    def __init__(self, result: GatewayDiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    def discover(self, _profile: object, *, force_refresh: bool = False) -> object:
        assert force_refresh is True
        self.calls += 1
        return self.result


class _MachineTransport:
    def __init__(self) -> None:
        self.calls = 0

    def get_json(
        self,
        _base_url: str,
        path: str,
        *,
        timeout_seconds: float,
    ) -> tuple[int, object]:
        assert timeout_seconds == 3.0
        self.calls += 1
        if path == "/health":
            return 200, None
        if path == "/models":
            return 200, {
                "object": "list",
                "data": [
                    {
                        "id": "GigaChat-2-Max",
                        "object": "model",
                        "owned_by": "sber",
                    }
                ],
            }
        assert path == "/bridge/capabilities"
        return 200, {
            "schema_version": "gpt2giga.route-support-matrix.v1",
            "matrix_revision": "sha256:" + "c" * 64,
            "cells": [
                {
                    "public_protocol": "openai_responses",
                    "upstream_provider": "gigachat",
                    "status": "technical_preview",
                    "reason_ids": ["normalized_responses_parity_incomplete"],
                    "evidence_ids": ["COR-01-CODEX-RESPONSES-2026-08-03"],
                }
            ],
        }


class _ProcessOwner:
    def __init__(self) -> None:
        self.refs: dict[str, NativeProcessRef] = {}
        self.spawns = 0

    def start(
        self,
        plan: NativeCommandPlan,
        *,
        session_id: str,
        workspace: str | None = None,
        run_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> NativeProcessRef:
        del workspace, timeout_seconds
        self.spawns += 1
        process_id = f"proc_{self.spawns}"
        ref = NativeProcessRef(
            id=process_id,
            pid=100,
            harness_id="gpt2giga-gateway",
            session_id=session_id,
            run_id=run_id or process_id,
            status=NativeProcessStatus.RUNNING,
            command=plan.command,
            display_command=plan.display_command,
            env={},
            cwd=plan.cwd,
            native_home=plan.native_home,
            transport="pipes",
            started_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
        )
        self.refs[process_id] = ref
        return ref

    def status(self, process_id: str) -> NativeProcessRef:
        return self.refs[process_id]

    def stop(self, process_id: str) -> NativeProcessRef:
        stopped = replace(self.refs[process_id], status=NativeProcessStatus.STOPPED)
        self.refs[process_id] = stopped
        return stopped


class _Readiness:
    def startup_ready(self, _base_url: str) -> bool:
        return True


class _Catalog:
    def get(self, project_id: str) -> object:
        assert project_id == "project-1"
        return SimpleNamespace(created_at=(NOW - timedelta(days=10)).isoformat())


class _EvidenceSessions:
    def list_sessions(self, **kwargs: object) -> tuple[object, ...]:
        assert kwargs["project_id"] == "project-1"
        return ()

    def list_runs_page(self, session_id: str, **kwargs: object) -> object:
        raise AssertionError(f"unexpected session read: {session_id}, {kwargs}")


class _EvidenceRuntime:
    def list_jobs_page(self, **kwargs: object) -> tuple[tuple[object, ...], bool]:
        assert kwargs["project_id"] == "project-1"
        return (), False

    def list_attempts(self, job_id: str | None = None) -> tuple[object, ...]:
        raise AssertionError(f"unexpected attempt read: {job_id}")

    def list_approval_requests(self, **_kwargs: object) -> tuple[object, ...]:
        return ()

    def list_policy_audit_events(self, **_kwargs: object) -> tuple[object, ...]:
        return ()


def capture(repository_root: Path, *, samples: int) -> dict[str, Any]:
    """Capture content-free, environment-matched workflow evidence."""
    if not 5 <= samples <= 50:
        raise ValueError("samples must be between 5 and 50")
    with ExitStack() as stack:
        temporary_root = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="gigaloom-09-perf-"))
        )
        cases = _cases(stack, temporary_root)
        results = [_measure(case, samples=samples) for case in cases]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_revision": _git(repository_root, "rev-parse", "HEAD"),
        "source_dirty": bool(_git(repository_root, "status", "--short")),
        "lock_sha256": hashlib.sha256(
            (repository_root / "uv.lock").read_bytes()
        ).hexdigest(),
        "environment": {
            "implementation": platform.python_implementation(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "samples_per_workload": samples,
        "privacy": {
            "content_free": True,
            "external_network_accessed": False,
            "native_homes_accessed": False,
            "provider_traffic": False,
            "temporary_state_only": True,
        },
        "results": results,
    }


def _cases(stack: ExitStack, root: Path) -> tuple[_Case, ...]:
    work_cases = _work_cases(stack, root / "work")
    relay_cases = _relay_cases(root / "relay")
    instruction_cases = _instruction_cases(root / "instructions")
    gateway_cases = _gateway_cases(root / "gateway")
    return (
        *work_cases,
        *relay_cases,
        _utf8_case(),
        *instruction_cases,
        *gateway_cases,
        _evidence_case(),
    )


def _work_cases(stack: ExitStack, root: Path) -> tuple[_Case, ...]:
    store = InMemoryHarnessSessionStore()
    registry = HarnessRegistry()
    registry.register(EchoHarness())
    stack.enter_context(
        patch(
            "gigaloom.ui.routers.catalog.proxy.discover_models",
            return_value=SimpleNamespace(
                ok=True,
                models=("TestModel",),
                source="content-free-fixture",
            ),
        )
    )
    with patch(
        "gigaloom.ui.container.GatewayRouteWebService.from_config",
        return_value=_StaticGatewayCatalog(),
    ):
        app = create_app(
            HarnessConfig(data_dir=str(root)),
            registry=registry,
            store=store,
        )
    client = stack.enter_context(TestClient(app))
    session = store.create_session(title="Performance fixture")
    run = store.create_run(
        session_id=session.id,
        harness_id="echo",
        prompt="content-free performance fixture",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
    )
    for index in range(240):
        store.append_event(
            HarnessStoredEvent(
                id=f"evt_{index:03d}",
                session_id=session.id,
                run_id=run.id,
                type="tool_call_finished",
                message=None,
                payload={"index": index},
                created_at=utc_now(),
            )
        )

    def initial_load() -> tuple[int, int]:
        total_bytes = 0
        for path in WORK_INITIAL_PATHS:
            response = client.get(path)
            response.raise_for_status()
            total_bytes += len(response.content)
        return len(WORK_INITIAL_PATHS), total_bytes

    def narrative_update() -> tuple[int, int]:
        response = client.get(f"/api/runs/{run.id}/trace?limit=200")
        response.raise_for_status()
        nodes = response.json()["nodes"]
        return 1, len(nodes)

    return (
        _Case(
            id="work.initial_load_request_graph",
            fixture={"route": "/web/work", "bounded_route_summary": True},
            operation=initial_load,
            details=lambda result: {
                "api_requests": float(result[0]),
                "response_bytes": float(result[1]),
            },
        ),
        _Case(
            id="work.run_narrative_update_isolation",
            fixture={"retained_events": 240, "trace_limit": 200},
            operation=narrative_update,
            details=lambda result: {
                "api_requests": float(result[0]),
                "projected_nodes": float(result[1]),
            },
        ),
    )


def _relay_cases(root: Path) -> tuple[_Case, ...]:
    store = FilesystemHarnessSessionStore(root)
    repository = ThreadDeliveryRepository(root)
    sessions = [
        store.create_session(
            title=f"Thread {index:03d}",
            default_harness_id="echo",
            default_model="TestModel",
            metadata={"actor_scope": "actor-1", "project_id": "project-1"},
        )
        for index in range(120)
    ]
    target = sessions[-1]
    for index in range(160):
        store.append_message(
            HarnessMessage(
                id=f"message-{index:03d}",
                session_id=target.id,
                run_id=None,
                role="assistant",
                content=f"visible fixture {index}",
                created_at=(NOW + timedelta(seconds=index)).isoformat(),
            )
        )
    actions = GigaLoomThreadRelayActions(
        scope=ThreadRelayScopeV1("actor-1", "project-1"),
        session_store=store,
        delivery_repository=repository,
        turn_submitter=_NoopSubmitter(),
        clock=lambda: NOW,
    )

    def list_threads() -> Mapping[str, object]:
        return actions.list_threads(source="gigaloom", cursor=None, limit=50)

    def read_thread() -> Mapping[str, object]:
        return actions.read_thread(
            source="gigaloom",
            thread_id=target.id,
            cursor=None,
            limit=50,
        )

    return (
        _Case(
            id="thread.list_page",
            fixture={"retained_threads": 120, "page_limit": 50},
            operation=list_threads,
            details=lambda result: {
                "items": float(len(result["threads"])),
                "has_more": float(bool(result["has_more"])),
            },
        ),
        _Case(
            id="thread.read_page",
            fixture={"retained_messages": 160, "page_limit": 50},
            operation=read_thread,
            details=lambda result: {
                "messages": float(len(result["thread"]["visible_messages"])),
                "has_more": float(result["thread"]["next_cursor"] is not None),
            },
        ),
    )


def _utf8_case() -> _Case:
    payload = ("# hello мир\nprint(42)\n" * 12_000).encode()
    return _Case(
        id="attachments.valid_utf8_decode",
        fixture={
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        operation=lambda: decode_attachment_text(payload),
        details=lambda result: {
            "replacement_count": 0.0,
            "truncated": 0.0,
        },
    )


def _instruction_cases(root: Path) -> tuple[_Case, ...]:
    small = _instruction_repository(root / "small", paths=32, instructions=4)
    large = _instruction_repository(root / "large", paths=2_000, instructions=128)

    def case(case_id: str, path: Path, paths: int, instructions: int) -> _Case:
        return _Case(
            id=case_id,
            fixture={
                "git_visible_paths": paths + instructions,
                "instruction_sources": instructions,
            },
            operation=lambda: instructions_api.discover_project_instructions(path),
            details=lambda result: {
                "scanned_paths": float(result.scanned_path_count),
                "sources": float(len(result.sources)),
                "truncated": float(result.scanned_paths_truncated),
            },
        )

    return (
        case("instructions.discovery_small", small, 32, 4),
        case("instructions.discovery_large", large, 2_000, 128),
    )


def _gateway_cases(root: Path) -> tuple[_Case, ...]:
    profile = _gateway_profile()
    managed_profile = replace(profile, mode=GatewayMode.MANAGED)
    route = _gateway_route()
    catalog = GatewayRouteCatalogV1(
        gateway_id=profile.gateway_id,
        profile_digest=profile.profile_digest,
        models_revision="sha256:" + "e" * 64,
        capabilities_revision="sha256:" + "b" * 64,
        loss_matrix_revision=route.loss_matrix_revision,
        routes=(route,),
        discovered_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(minutes=5)).isoformat(),
        catalog_digest="f" * 64,
    )
    static = _StaticDiscovery(
        GatewayDiscoveryResult(GatewayDiscoveryStatus.CURRENT, catalog)
    )
    preflight = GatewayRouteWebService(
        profile,
        static,
        artifact_resolver=lambda _profile: None,
        clock=lambda: NOW,
    )
    transport_holder: dict[str, object] = {}
    cold_holder: dict[str, object] = {}
    warm_holder: dict[str, object] = {}
    executable = root / "bin" / "gpt2giga"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    artifact = GatewayArtifactEvidenceV1(
        distribution="gpt2giga",
        version="0.3.0",
        artifact_sha256=profile.artifact_sha256,
        executable_path=str(executable),
        source="locked-registry:gpt2giga-0.3.0",
        verified=True,
    )

    def reset_discovery() -> None:
        transport = _MachineTransport()
        transport_holder["transport"] = transport
        transport_holder["discovery"] = GatewayRouteDiscovery(
            transport, clock=lambda: NOW
        )

    def route_discovery() -> object:
        return transport_holder["discovery"].discover(profile)

    def reset_sidecar(holder: dict[str, object], *, warm: bool) -> None:
        owner = _ProcessOwner()
        service = ManagedGatewaySidecarService(
            owner,
            _Readiness(),
            managed_data_root=root / ("warm" if warm else "cold"),
        )
        holder["owner"] = owner
        holder["service"] = service
        if warm:
            service.ensure_started(
                managed_profile,
                artifact,
                environment={"PATH": "/usr/bin"},
                session_id="session-warmup",
                run_id="run-warmup",
            )
            owner.spawns = 0

    def sidecar(holder: dict[str, object], run_id: str) -> object:
        return holder["service"].ensure_started(
            managed_profile,
            artifact,
            environment={"PATH": "/usr/bin"},
            session_id="session-performance",
            run_id=run_id,
        )

    return (
        _Case(
            id="gateway.preflight",
            fixture={"routes": 1, "force_refresh": True},
            before_each=lambda: setattr(static, "calls", 0),
            operation=lambda: preflight.preflight(
                route.route_id, acknowledgement_id=None
            ),
            details=lambda result: {
                "discovery_calls": float(static.calls),
                "ready": float(result["status"] == "ready"),
            },
        ),
        _Case(
            id="gateway.route_model_discovery",
            fixture={"models": 1, "capability_cells": 1},
            before_each=reset_discovery,
            operation=route_discovery,
            details=lambda result: {
                "transport_calls": float(transport_holder["transport"].calls),
                "routes": float(len(result.catalog.routes)),
            },
        ),
        _Case(
            id="gateway.sidecar_cold_start",
            fixture={"verified_artifact": True, "readiness_checks": 1},
            before_each=lambda: reset_sidecar(cold_holder, warm=False),
            operation=lambda: sidecar(cold_holder, "run-cold"),
            details=lambda result: {
                "process_spawns": float(cold_holder["owner"].spawns),
                "started": float(result.status is GatewaySidecarStatus.STARTED),
            },
        ),
        _Case(
            id="gateway.sidecar_warm_attach",
            fixture={"verified_artifact": True, "existing_lease": True},
            before_each=lambda: reset_sidecar(warm_holder, warm=True),
            operation=lambda: sidecar(warm_holder, "run-warm"),
            details=lambda result: {
                "process_spawns": float(warm_holder["owner"].spawns),
                "reused": float(result.status is GatewaySidecarStatus.REUSED),
            },
        ),
    )


def _evidence_case() -> _Case:
    application = ProductEvidenceApplication(
        project_catalog=_Catalog(),
        session_owner=_EvidenceSessions(),
        runtime_owner=_EvidenceRuntime(),
    )

    def export() -> bytes:
        report = application.report(
            project_id="project-1",
            range_start=NOW - timedelta(days=30),
            range_end=NOW,
            generated_at=NOW,
        )
        return json.dumps(
            product_evidence_report_to_dict(report),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    return _Case(
        id="evidence.local_export",
        fixture={"sessions": 0, "network_sinks": 0},
        operation=export,
        details=lambda result: {
            "bytes": float(len(result)),
            "network_calls": 0.0,
        },
    )


def _measure(case: _Case, *, samples: int) -> dict[str, Any]:
    for _ in range(3):
        case.before_each()
        case.operation()
    elapsed: list[float] = []
    detail_samples: dict[str, list[float]] = {}
    for _ in range(samples):
        case.before_each()
        started = perf_counter()
        result = case.operation()
        elapsed.append((perf_counter() - started) * 1_000)
        for key, value in case.details(result).items():
            detail_samples.setdefault(key, []).append(value)
    return {
        "id": case.id,
        "fixture": dict(case.fixture),
        "latency_ms": _summary(elapsed),
        "counters": {
            key: _summary(values) for key, values in sorted(detail_samples.items())
        },
    }


def _summary(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50": round(_percentile(ordered, 0.50), 6),
        "p95": round(_percentile(ordered, 0.95), 6),
    }


def _percentile(ordered: list[float], quantile: float) -> float:
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _instruction_repository(
    root: Path,
    *,
    paths: int,
    instructions: int,
) -> Path:
    root.mkdir(parents=True)
    _run(("git", "init", "-q", "-b", "main"), cwd=root)
    for index in range(paths):
        path = root / "src" / f"file-{index:04d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    for index in range(instructions):
        path = root / f"scope-{index:03d}" / "AGENTS.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content-free fixture\n", encoding="utf-8")
    _run(("git", "add", "."), cwd=root)
    _run(
        (
            "git",
            "-c",
            "user.name=Performance Fixture",
            "-c",
            "user.email=performance@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ),
        cwd=root,
    )
    return root


def _gateway_profile() -> GatewayProfileV1:
    return GatewayProfileV1(
        gateway_id="gpt2giga",
        display_name="gpt2giga 0.3",
        mode=GatewayMode.EXTERNAL,
        distribution="gpt2giga",
        executable="gpt2giga",
        version="0.3.0",
        version_window=">=0.3.0,<0.4.0",
        artifact_sha256="8" * 64,
        base_url="http://127.0.0.1:8090",
        startup_config_revision="sha256:" + "1" * 64,
        health_contract_revision="gpt2giga.health.v1",
        readiness_contract_revision="gpt2giga.readiness.v1",
        models_contract_revision="openai.models.v1",
        capabilities_contract_revision="gpt2giga.route-support-matrix.v1",
        auth_ref=None,
        tls_policy_ref="tls-policy:loopback",
        profile_digest="a" * 64,
    )


def _gateway_route() -> BridgeRouteV1:
    return BridgeRouteV1(
        route_id="codex-gpt2giga-gigachat-2-max",
        agent_id="codex",
        client_protocol="openai_responses",
        gateway_profile_id="gpt2giga",
        public_model_alias="GigaChat-2-Max",
        upstream_provider="gigachat",
        upstream_model="GigaChat-2-Max",
        capability_profile_revision="sha256:" + "b" * 64,
        loss_matrix_revision="sha256:" + "c" * 64,
        support_status=GatewaySupportStatus.STABLE,
        reason_ids=(),
        evidence_ids=("fixture",),
    )


def _run(command: tuple[str, ...], *, cwd: Path) -> None:
    subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _git(repository_root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def _add_comparison(after: dict[str, Any], before: dict[str, Any]) -> None:
    if before["schema_version"] != after["schema_version"]:
        raise ValueError("baseline schema does not match")
    for key in ("lock_sha256", "environment", "samples_per_workload"):
        if before[key] != after[key]:
            raise ValueError(f"baseline {key} does not match")
    before_results = {item["id"]: item for item in before["results"]}
    if set(before_results) != {item["id"] for item in after["results"]}:
        raise ValueError("baseline workload ids do not match")
    for result in after["results"]:
        baseline = before_results[result["id"]]
        if baseline["fixture"] != result["fixture"]:
            raise ValueError(f"baseline fixture changed for {result['id']}")
        result["change_pct"] = {
            percentile: round(
                (baseline["latency_ms"][percentile] - result["latency_ms"][percentile])
                / baseline["latency_ms"][percentile]
                * 100,
                6,
            )
            for percentile in ("p50", "p95")
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", default=20, type=int)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    repository_root = Path.cwd().resolve()
    result = {"label": args.label, **capture(repository_root, samples=args.samples)}
    if args.baseline is not None:
        before = json.loads(args.baseline.read_text(encoding="utf-8"))
        _add_comparison(result, before)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
