"""Web composition for reviewed gateway routes and exact run bindings."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event, Lock

import pytest
from fastapi.testclient import TestClient

from gigaloom.cli_commands.gateway_transport import (
    AuthenticatedGatewayMachineTransport,
)
from gigaloom.config import HarnessConfig
from gigaloom.native.api import GatewayMode, GatewayProfileV1, GatewayRouteDiscovery
from gigaloom.native.launch.gateway_sidecar import (
    GatewayArtifactEvidenceV1,
    GatewaySidecarReason,
    GatewaySidecarStatus,
    ManagedGatewayLeaseV1,
)
from gigaloom.tools import THREAD_RELAY_TOOL_IDS
from gigaloom.ui.app import create_app
from gigaloom.ui.dependencies import app_services
from gigaloom.ui.services.gateway_routes import (
    GatewayRouteStartError,
    GatewayRouteWebService,
)


class _Transport:
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
                "data": [{"id": "GigaChat-2-Max", "owned_by": "sber"}],
            }
        return 200, {
            "schema_version": "gpt2giga.route-support-matrix.v1",
            "matrix_revision": "sha256:" + "c" * 64,
            "cells": [
                {
                    "public_protocol": "openai_chat_completions",
                    "upstream_provider": "gigachat",
                    "status": "stable",
                    "reason_ids": ["baseline_gigachat_chat_conformance"],
                    "evidence_ids": ["gigachat-chat-baseline-2026-08-03"],
                },
                {
                    "public_protocol": "openai_responses",
                    "upstream_provider": "gigachat",
                    "status": "technical_preview",
                    "reason_ids": ["normalized_responses_parity_incomplete"],
                    "evidence_ids": ["COR-01-CODEX-RESPONSES-2026-08-03"],
                },
            ],
        }


class _Sidecar:
    def __init__(self, *leases: ManagedGatewayLeaseV1) -> None:
        self.leases = list(leases)
        self.ensure_calls: list[tuple[str, str, dict[str, str]]] = []
        self.stop_calls = 0

    def ensure_started(
        self,
        profile,
        artifact,
        *,
        environment,
        session_id,
        run_id,
    ):
        del profile, artifact
        self.ensure_calls.append((session_id, run_id, dict(environment)))
        return self.leases.pop(0)

    def stop(self, profile):
        self.stop_calls += 1
        return _lease(profile, GatewaySidecarStatus.STOPPED, ready=False)


class _ConcurrentSidecar(_Sidecar):
    def __init__(self, *leases: ManagedGatewayLeaseV1) -> None:
        super().__init__(*leases)
        self.first_entered = Event()
        self.second_entered = Event()
        self.release_first = Event()
        self._entry_lock = Lock()
        self._entry_count = 0

    def ensure_started(self, *args, **kwargs):
        with self._entry_lock:
            self._entry_count += 1
            call_number = self._entry_count
        if call_number == 1:
            self.first_entered.set()
            self.release_first.wait(timeout=1)
        else:
            self.second_entered.set()
        return super().ensure_started(*args, **kwargs)


def _profile(mode: GatewayMode = GatewayMode.EXTERNAL) -> GatewayProfileV1:
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
        auth_ref=None,
        tls_policy_ref="tls-policy:loopback",
        profile_digest="a" * 64,
    )


def _artifact() -> GatewayArtifactEvidenceV1:
    return GatewayArtifactEvidenceV1(
        distribution="gpt2giga",
        version="0.3.0",
        artifact_sha256="8" * 64,
        executable_path="/verified/gpt2giga",
        source="locked-registry:pypi/gpt2giga==0.3.0",
        verified=True,
    )


def _lease(
    profile: GatewayProfileV1,
    status: GatewaySidecarStatus,
    *,
    ready: bool,
    reason: GatewaySidecarReason | None = None,
) -> ManagedGatewayLeaseV1:
    return ManagedGatewayLeaseV1(
        gateway_id=profile.gateway_id,
        profile_digest=profile.profile_digest,
        status=status,
        process_lease_ref="native-process:proc_gateway",
        managed_root="/managed/gateway",
        startup_config_ref="managed-config:startup.json",
        readiness_confirmed=ready,
        reason=reason,
        observed_artifact_sha256="8" * 64,
    )


def test_catalog_preflight_and_submission_share_exact_current_revisions() -> None:
    now = [datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)]
    service = GatewayRouteWebService(
        _profile(),
        GatewayRouteDiscovery(_Transport(), clock=lambda: now[0]),
        clock=lambda: now[0],
    )

    catalog = service.catalog()
    route = next(item for item in catalog["routes"] if item["agent_id"] == "acp")
    assert route["client_protocol"] == "openai_chat_completions"
    assert route["support_status"] == "stable"
    receipt = service.preflight(route["route_id"], acknowledgement_id=None)
    binding = {
        "schema_version": 1,
        "route_id": route["route_id"],
        "agent_id": route["agent_id"],
        "gateway_profile_id": route["gateway_profile_id"],
        "public_model_alias": route["public_model_alias"],
        "support_status": route["support_status"],
        "acknowledgement_id": None,
        "preflight_receipt_id": receipt["receipt_id"],
        "preflight_checked_at": receipt["checked_at"],
        "profile_digest": receipt["profile_digest"],
        "artifact_sha256": receipt["artifact_sha256"],
        "capability_profile_revision": route["capability_profile_revision"],
        "models_revision": receipt["models_revision"],
        "loss_matrix_revision": route["loss_matrix_revision"],
    }

    bound = service.bind_submission(
        {"route_id": route["route_id"], "gateway_route_binding": binding}
    )

    assert bound["extra"] == {"gateway_route_binding": binding}
    assert route["gateway_display_name"] == "gpt2giga 0.3"
    assert catalog["lifecycle"] == {
        "mode": "external",
        "start_available": False,
    }

    with pytest.raises(ValueError, match="no longer matches"):
        service.bind_submission(
            {
                "route_id": route["route_id"],
                "gateway_route_binding": {**binding, "agent_id": "claude"},
            }
        )
    now[0] += timedelta(minutes=6)
    with pytest.raises(ValueError, match="expired"):
        service.bind_submission(
            {"route_id": route["route_id"], "gateway_route_binding": binding}
        )


def test_explicit_start_reuses_ready_sidecar_and_refreshes_catalog() -> None:
    profile = _profile(GatewayMode.MANAGED)
    transport = _Transport()
    sidecar = _Sidecar(
        _lease(profile, GatewaySidecarStatus.STARTED, ready=True),
        _lease(profile, GatewaySidecarStatus.REUSED, ready=True),
    )
    service = GatewayRouteWebService(
        profile,
        GatewayRouteDiscovery(transport),
        artifact_resolver=lambda _profile: _artifact(),
        sidecar=sidecar,  # type: ignore[arg-type]
        sidecar_environment=lambda: {
            "GPT2GIGA_API_KEY": "gateway-secret",
            "GIGACHAT_ACCESS_TOKEN": "upstream-secret",
        },
        gateway_api_key="gateway-secret",
    )

    started = service.start(session_id="sess_existing_123")
    reused = service.start(session_id="sess_existing_123")

    assert started["status"] == "started"
    assert reused["status"] == "reused"
    assert started["catalog"]["status"] == "current"
    assert any(
        route["client_protocol"] == "openai_chat_completions"
        for route in started["catalog"]["routes"]
    )
    assert [call[:2] for call in sidecar.ensure_calls] == [
        ("sess_existing_123", "gateway-route-gpt2giga"),
        ("sess_existing_123", "gateway-route-gpt2giga"),
    ]
    assert transport.calls.count("/health") == 2
    assert "gateway-secret" not in repr(started)
    assert "upstream-secret" not in repr(started)


def test_concurrent_start_requests_are_serialized_by_the_web_owner() -> None:
    profile = _profile(GatewayMode.MANAGED)
    sidecar = _ConcurrentSidecar(
        _lease(profile, GatewaySidecarStatus.STARTED, ready=True),
        _lease(profile, GatewaySidecarStatus.REUSED, ready=True),
    )
    service = GatewayRouteWebService(
        profile,
        GatewayRouteDiscovery(_Transport()),
        artifact_resolver=lambda _profile: _artifact(),
        sidecar=sidecar,  # type: ignore[arg-type]
        sidecar_environment=lambda: {"PATH": "/usr/bin"},
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.start, session_id="sess_existing_123")
        assert sidecar.first_entered.wait(timeout=1)
        second = pool.submit(service.start, session_id="sess_existing_123")
        try:
            assert not sidecar.second_entered.wait(timeout=0.1)
        finally:
            sidecar.release_first.set()
        assert first.result(timeout=1)["status"] == "started"
        assert second.result(timeout=1)["status"] == "reused"

    assert sidecar.second_entered.is_set()
    assert len(sidecar.ensure_calls) == 2


def test_explicit_start_fails_closed_before_spawn() -> None:
    profile = _profile(GatewayMode.MANAGED)
    blocked = _Sidecar(
        _lease(
            profile,
            GatewaySidecarStatus.BLOCKED,
            ready=False,
            reason=GatewaySidecarReason.STARTUP_READINESS_TIMEOUT,
        )
    )
    service = GatewayRouteWebService(
        profile,
        GatewayRouteDiscovery(_Transport()),
        artifact_resolver=lambda _profile: _artifact(),
        sidecar=blocked,  # type: ignore[arg-type]
        sidecar_environment=lambda: {"PATH": "/usr/bin"},
        gateway_api_key="gateway-secret",
    )

    with pytest.raises(
        GatewayRouteStartError,
        match=GatewaySidecarReason.STARTUP_READINESS_TIMEOUT.value,
    ):
        service.start(session_id="sess_existing_123")

    unverified = GatewayRouteWebService(
        profile,
        GatewayRouteDiscovery(_Transport()),
        artifact_resolver=lambda _profile: None,
        sidecar=blocked,  # type: ignore[arg-type]
        sidecar_environment=lambda: {"PATH": "/usr/bin"},
    )
    with pytest.raises(GatewayRouteStartError, match="gateway_artifact_unverified"):
        unverified.start(session_id="sess_existing_123")
    assert len(blocked.ensure_calls) == 1


def test_app_shutdown_stops_only_the_owned_gateway_lease(tmp_path) -> None:
    app = create_app(HarnessConfig(data_dir=tmp_path))
    service = app_services(app).gateway_route_service
    sidecar = _Sidecar()
    service.sidecar = sidecar  # type: ignore[assignment]

    with TestClient(app):
        pass

    assert sidecar.stop_calls == 1


def test_root_app_mounts_wave_b_routes_and_one_scoped_thread_tool_owner(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GIGACHAT_ACCESS_TOKEN", "fixture-upstream-secret")
    app = create_app(HarnessConfig(data_dir=tmp_path))
    services = app_services(app)
    pending = list(app.routes)
    paths: set[str] = set()
    while pending:
        route = pending.pop()
        included = getattr(route, "original_router", None)
        if included is not None:
            pending.extend(included.routes)
        elif isinstance(path := getattr(route, "path", None), str):
            paths.add(path)

    assert {
        "/api/evidence/product-beta",
        "/api/gateway/routes",
        "/api/gateway/routes/start",
        "/api/gateway/routes/{route_id}/preflight",
        "/api/thread-relay/threads",
        "/api/thread-relay/deliveries",
    } <= paths
    first = services.thread_relay.tools("actor-1", "project-1")
    second = services.thread_relay.tools("actor-1", "project-1")
    assert first is second
    assert tuple(item.id for item in first.list_tools()) == THREAD_RELAY_TOOL_IDS
    gateway = services.gateway_route_service
    transport = gateway.discovery._transport  # noqa: SLF001
    assert isinstance(transport, AuthenticatedGatewayMachineTransport)
    assert gateway.gateway_api_key == transport.api_key == services.config.api_key
    assert gateway.sidecar_environment is not None
    environment = gateway.sidecar_environment()
    assert environment["GPT2GIGA_API_KEY"] == gateway.gateway_api_key
