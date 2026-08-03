"""Public machine-contract discovery for reviewed gateway routes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from gigaloom.native.api import (
    GatewayDiscoveryReason,
    GatewayDiscoveryStatus,
    GatewayMode,
    GatewayProfileV1,
    GatewayRouteDiscovery,
    GatewaySupportStatus,
)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.failure_path: str | None = None

    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        timeout_seconds: float,
    ) -> tuple[int, object]:
        self.calls.append((base_url, path, timeout_seconds))
        if path == self.failure_path:
            raise OSError("fixture unavailable")
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
                },
                {
                    "public_protocol": "openai_responses",
                    "upstream_provider": "anthropic",
                    "status": "blocked",
                    "reason_ids": ["provider_not_supported"],
                    "evidence_ids": [],
                },
            ],
        }


def _profile() -> GatewayProfileV1:
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


def test_discovery_uses_only_public_get_contracts_and_builds_exact_route() -> None:
    transport = FakeTransport()
    now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    discovery = GatewayRouteDiscovery(transport, clock=lambda: now, ttl_seconds=60)

    result = discovery.discover(_profile())

    assert result.status is GatewayDiscoveryStatus.CURRENT
    assert result.catalog is not None
    assert transport.calls == [
        ("http://127.0.0.1:8090", "/health", 3.0),
        ("http://127.0.0.1:8090", "/models", 3.0),
        ("http://127.0.0.1:8090", "/bridge/capabilities", 3.0),
    ]
    assert len(result.catalog.catalog_digest) == 64
    assert result.catalog.models_revision.startswith("sha256:")
    assert result.catalog.loss_matrix_revision == "sha256:" + "c" * 64
    assert len(result.catalog.routes) == 1
    route = result.catalog.routes[0]
    assert route.route_id == "codex-gpt2giga-gigachat-2-max"
    assert route.agent_id == "codex"
    assert route.client_protocol == "openai_responses"
    assert route.public_model_alias == "GigaChat-2-Max"
    assert route.upstream_provider == "gigachat"
    assert route.support_status is GatewaySupportStatus.TECHNICAL_PREVIEW


def test_fresh_cache_avoids_network_and_profile_digest_partitions_entries() -> None:
    transport = FakeTransport()
    now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    discovery = GatewayRouteDiscovery(transport, clock=lambda: now)

    first = discovery.discover(_profile())
    second = discovery.discover(_profile())

    assert second == first
    assert len(transport.calls) == 3
    changed = replace(_profile(), profile_digest="b" * 64)
    discovery.discover(changed)
    assert len(transport.calls) == 6


def test_expired_cache_is_returned_only_as_stale_and_first_failure_is_unknown() -> None:
    transport = FakeTransport()
    now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    current = [now]
    discovery = GatewayRouteDiscovery(
        transport,
        clock=lambda: current[0],
        ttl_seconds=1,
    )
    fresh = discovery.discover(_profile())
    assert fresh.catalog is not None

    current[0] = now + timedelta(seconds=2)
    transport.failure_path = "/models"
    stale = discovery.discover(_profile())
    assert stale.status is GatewayDiscoveryStatus.STALE
    assert stale.catalog == fresh.catalog
    assert stale.reason_ids == (GatewayDiscoveryReason.MODELS_UNAVAILABLE,)

    unknown_transport = FakeTransport()
    unknown_transport.failure_path = "/health"
    unknown = GatewayRouteDiscovery(unknown_transport).discover(_profile())
    assert unknown.status is GatewayDiscoveryStatus.UNKNOWN
    assert unknown.catalog is None
    assert unknown.reason_ids == (GatewayDiscoveryReason.HEALTH_UNAVAILABLE,)


def test_unknown_capability_schema_fails_closed_without_routes() -> None:
    class Drifted(FakeTransport):
        def get_json(
            self,
            base_url: str,
            path: str,
            *,
            timeout_seconds: float,
        ) -> tuple[int, object]:
            status, payload = super().get_json(
                base_url,
                path,
                timeout_seconds=timeout_seconds,
            )
            if path == "/bridge/capabilities":
                assert isinstance(payload, dict)
                payload = {**payload, "schema_version": "gpt2giga.future.v2"}
            return status, payload

    result = GatewayRouteDiscovery(Drifted()).discover(_profile())
    assert result.status is GatewayDiscoveryStatus.UNKNOWN
    assert result.reason_ids == (GatewayDiscoveryReason.CONTRACT_REVISION_MISMATCH,)
