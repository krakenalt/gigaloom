"""Route-local gateway diagnostics and lifecycle operator controls."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
from typing import cast

from gigaloom.cli_commands.commands.gateway import register
from gigaloom.cli_commands.handlers.gateway import (
    GatewayCommandHandlers,
    GatewayCommandService,
)
from gigaloom.config import HarnessConfig
from gigaloom.native.api import (
    GatewayArtifactEvidenceV1,
    GatewayMode,
    GatewayProfileV1,
    GatewayRouteDiscovery,
    GatewaySidecarReason,
    GatewaySidecarStatus,
    ManagedGatewayLeaseV1,
)


class MachineTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, base_url, path, *, timeout_seconds):
        del base_url, timeout_seconds
        self.calls.append(path)
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


class HealthProbe:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[str] = []

    def startup_ready(self, base_url: str) -> bool:
        self.calls.append(base_url)
        return self.ready


class Sidecar:
    def __init__(self) -> None:
        self.started: list[tuple[str, tuple[str, ...]]] = []
        self.stopped: list[str] = []

    def ensure_started(
        self,
        profile,
        artifact,
        *,
        environment,
        session_id,
        run_id,
    ):
        del artifact, environment
        self.started.append((profile.gateway_id, (session_id, run_id)))
        return _lease(profile, GatewaySidecarStatus.STARTED)

    def stop(self, profile):
        self.stopped.append(profile.gateway_id)
        return _lease(profile, GatewaySidecarStatus.STOPPED)


def _profile(*, mode: GatewayMode = GatewayMode.MANAGED) -> GatewayProfileV1:
    return GatewayProfileV1(
        gateway_id="gpt2giga",
        display_name="gpt2giga 0.3",
        mode=mode,
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
        auth_ref="secret-ref:gigachat",
        tls_policy_ref="tls-policy:loopback",
        profile_digest="a" * 64,
    )


def _artifact(executable: Path) -> GatewayArtifactEvidenceV1:
    return GatewayArtifactEvidenceV1(
        distribution="gpt2giga",
        version="0.3.0",
        artifact_sha256="8" * 64,
        executable_path=str(executable),
        source="locked-registry:gpt2giga-0.3.0",
        verified=True,
    )


def _executable(tmp_path: Path) -> Path:
    value = tmp_path / "gpt2giga"
    value.write_text("#!/bin/sh\n", encoding="utf-8")
    value.chmod(0o755)
    return value


def _service(tmp_path: Path, *, mode: GatewayMode = GatewayMode.MANAGED):
    profile = _profile(mode=mode)
    artifact = _artifact(_executable(tmp_path))
    transport = MachineTransport()
    health = HealthProbe()
    sidecar = Sidecar()
    service = GatewayCommandService.create(
        (profile,),
        discovery=GatewayRouteDiscovery(transport),
        readiness_probe=health,
        artifact_resolver=lambda _profile: artifact,
        sidecar=sidecar,
    )
    return service, transport, health, sidecar


def test_route_local_parser_exposes_only_operator_command_family() -> None:
    parser = argparse.ArgumentParser(prog="giga")
    register(parser.add_subparsers(dest="command"))

    assert parser.parse_args(["gateway", "list"]).handler == "_handle_gateway_list"
    inspect = parser.parse_args(["gateway", "inspect", "gpt2giga", "--refresh"])
    assert inspect.handler == "_handle_gateway_inspect"
    assert inspect.gateway_id == "gpt2giga"
    assert inspect.refresh is True
    assert parser.parse_args(["gateway", "doctor", "gpt2giga"]).handler == (
        "_handle_gateway_doctor"
    )
    assert parser.parse_args(["gateway", "start", "gpt2giga"]).handler == (
        "_handle_gateway_start"
    )
    assert parser.parse_args(["gateway", "stop", "gpt2giga"]).handler == (
        "_handle_gateway_stop"
    )


def test_list_is_sorted_and_performs_no_network_or_process_activity(
    tmp_path: Path,
) -> None:
    service, transport, health, sidecar = _service(tmp_path)

    payload = service.list()

    assert payload["gateways"][0]["gateway_id"] == "gpt2giga"
    assert transport.calls == []
    assert health.calls == []
    assert sidecar.started == []


def test_inspect_reads_only_public_machine_contracts_and_projects_routes(
    tmp_path: Path,
) -> None:
    service, transport, _health, sidecar = _service(tmp_path)

    payload = service.inspect("gpt2giga", refresh=True)

    assert transport.calls == ["/models", "/bridge/capabilities"]
    assert payload["discovery_status"] == "current"
    assert payload["catalog"]["routes"][0]["route_id"] == (
        "codex-gpt2giga-gigachat-2-max"
    )
    assert sidecar.started == []


def test_doctor_checks_exact_artifact_and_health_without_model_discovery(
    tmp_path: Path,
) -> None:
    service, transport, health, sidecar = _service(tmp_path)

    payload = service.doctor("gpt2giga")

    assert payload["ready"] is True
    assert payload["artifact_state"] == "verified"
    assert payload["provider_inference_performed"] is False
    assert health.calls == ["http://127.0.0.1:8090"]
    assert transport.calls == []
    assert sidecar.started == []


def test_external_doctor_does_not_require_local_artifact(tmp_path: Path) -> None:
    service, transport, _health, _sidecar = _service(
        tmp_path,
        mode=GatewayMode.EXTERNAL,
    )
    resolver_calls: list[str] = []
    service = replace(
        service,
        artifact_resolver=lambda profile: resolver_calls.append(profile.gateway_id),
    )

    payload = service.doctor("gpt2giga")

    assert payload["artifact_state"] == "not_applicable"
    assert payload["ready"] is True
    assert resolver_calls == []
    assert transport.calls == []


def test_drifted_artifact_fails_doctor_closed(tmp_path: Path) -> None:
    service, _transport, _health, _sidecar = _service(tmp_path)
    artifact = _artifact(tmp_path / "missing-gpt2giga")
    drifted = replace(service, artifact_resolver=lambda _profile: artifact)

    payload = drifted.doctor("gpt2giga")

    assert payload["ready"] is False
    assert payload["artifact_state"] == "executable_unavailable"


def test_start_and_stop_delegate_only_to_existing_profile_scoped_lease(
    tmp_path: Path,
) -> None:
    service, transport, _health, sidecar = _service(tmp_path)

    started = service.start(
        "gpt2giga",
        environment={"PATH": "/usr/bin"},
        session_id="session-1",
        run_id="run-1",
    )
    stopped = service.stop("gpt2giga")

    assert started["status"] == "started"
    assert stopped["status"] == "stopped"
    assert sidecar.started == [("gpt2giga", ("session-1", "run-1"))]
    assert sidecar.stopped == ["gpt2giga"]
    assert transport.calls == []


def test_bound_doctor_handler_emits_machine_stable_json_and_exit_code(
    tmp_path: Path,
    capsys,
) -> None:
    service, _transport, _health, _sidecar = _service(tmp_path)
    args = argparse.Namespace(gateway_id="gpt2giga", json=True)

    exit_code = GatewayCommandHandlers(service).doctor(
        args,
        cast(HarnessConfig, object()),
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["gateway_id"] == "gpt2giga"
    assert payload["provider_inference_performed"] is False


def test_unknown_gateway_fails_without_network_or_process_activity(
    tmp_path: Path,
) -> None:
    service, transport, _health, sidecar = _service(tmp_path)

    try:
        service.inspect("missing", refresh=False)
    except ValueError as error:
        assert str(error) == "unknown gateway profile: missing"
    else:
        raise AssertionError("unknown gateway must fail")

    assert transport.calls == []
    assert sidecar.started == []


def _lease(
    profile: GatewayProfileV1,
    status: GatewaySidecarStatus,
) -> ManagedGatewayLeaseV1:
    return ManagedGatewayLeaseV1(
        gateway_id=profile.gateway_id,
        profile_digest=profile.profile_digest,
        status=status,
        process_lease_ref="native-process:gateway-1",
        managed_root="/managed/gateways/gpt2giga",
        startup_config_ref="managed-config:startup.json",
        readiness_confirmed=status is GatewaySidecarStatus.STARTED,
        reason=(
            GatewaySidecarReason.LEASE_NOT_FOUND
            if status is GatewaySidecarStatus.BLOCKED
            else None
        ),
    )
