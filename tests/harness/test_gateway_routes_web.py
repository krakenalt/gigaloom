"""Web composition for reviewed gateway routes and exact run bindings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gigaloom.config import HarnessConfig
from gigaloom.native.api import GatewayMode, GatewayProfileV1, GatewayRouteDiscovery
from gigaloom.tools import THREAD_RELAY_TOOL_IDS
from gigaloom.ui.app import create_app
from gigaloom.ui.dependencies import app_services
from gigaloom.ui.services.gateway_routes import GatewayRouteWebService


class _Transport:
    def get_json(self, base_url, path, *, timeout_seconds):
        del base_url, timeout_seconds
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
                    "public_protocol": "openai_responses",
                    "upstream_provider": "gigachat",
                    "status": "technical_preview",
                    "reason_ids": ["normalized_responses_parity_incomplete"],
                    "evidence_ids": ["COR-01-CODEX-RESPONSES-2026-08-03"],
                }
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


def test_catalog_preflight_and_submission_share_exact_current_revisions() -> None:
    now = [datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)]
    service = GatewayRouteWebService(
        _profile(),
        GatewayRouteDiscovery(_Transport(), clock=lambda: now[0]),
        clock=lambda: now[0],
    )

    catalog = service.catalog()
    route = catalog["routes"][0]
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


def test_root_app_mounts_wave_b_routes_and_one_scoped_thread_tool_owner(
    tmp_path,
) -> None:
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
        "/api/gateway/routes/{route_id}/preflight",
        "/api/thread-relay/threads",
        "/api/thread-relay/deliveries",
    } <= paths
    first = services.thread_relay.tools("actor-1", "project-1")
    second = services.thread_relay.tools("actor-1", "project-1")
    assert first is second
    assert tuple(item.id for item in first.list_tools()) == THREAD_RELAY_TOOL_IDS
