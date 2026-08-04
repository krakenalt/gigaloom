"""Contracts for exact, redaction-safe gateway launch overlays."""

from __future__ import annotations

from dataclasses import replace

import pytest

from gigaloom.native.api import (
    BridgeRouteV1,
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
    GatewaySupportStatus,
    LaunchOverlayV1,
    bridge_route_from_dict,
    bridge_route_to_dict,
    gateway_contract_digest,
    gateway_preflight_receipt_from_dict,
    gateway_preflight_receipt_to_dict,
    gateway_profile_from_dict,
    gateway_profile_to_dict,
    launch_overlay_from_dict,
    launch_overlay_to_dict,
)


def _profile() -> GatewayProfileV1:
    return GatewayProfileV1(
        gateway_id="gpt2giga",
        display_name="gpt2giga 0.3",
        mode=GatewayMode.MANAGED,
        distribution="gpt2giga",
        executable="gpt2giga",
        version="0.3.0",
        version_window=">=0.3.0,<0.4.0",
        artifact_sha256="8" * 64,
        base_url="http://127.0.0.1:8090",
        startup_config_revision="sha256:" + "1" * 64,
        health_contract_revision="gpt2giga.health.v1",
        readiness_contract_revision="gpt2giga.readiness.v1",
        models_contract_revision="gpt2giga.models.v1",
        capabilities_contract_revision="gpt2giga.effective-capabilities.v1",
        auth_ref="secret-ref:gpt2giga",
        tls_policy_ref="tls-policy:loopback",
        profile_digest="a" * 64,
    )


def _route() -> BridgeRouteV1:
    return BridgeRouteV1(
        route_id="codex-gpt2giga-gigachat-max",
        agent_id="codex",
        client_protocol="openai_responses",
        gateway_profile_id="gpt2giga",
        public_model_alias="GigaChat-2-Max",
        upstream_provider="gigachat",
        upstream_model="GigaChat-2-Max",
        capability_profile_revision="sha256:" + "b" * 64,
        loss_matrix_revision="sha256:" + "c" * 64,
        support_status=GatewaySupportStatus.TECHNICAL_PREVIEW,
        reason_ids=("normalized_responses_parity_incomplete",),
        evidence_ids=("COR-01-CODEX-RESPONSES-2026-08-03",),
    )


def _overlay() -> LaunchOverlayV1:
    return LaunchOverlayV1(
        route_id="codex-gpt2giga-gigachat-max",
        managed_home="/tmp/gigaloom/gateway/codex",
        redacted_env_delta=(
            ("OPENAI_API_KEY", "<secret-ref>"),
            ("OPENAI_BASE_URL", "http://127.0.0.1:8090/v2"),
        ),
        generated_config_refs=("managed-config:codex/config.toml",),
        process_lease_ref="native-process:gateway-01",
        preflight_receipt_ref="gateway-preflight:receipt-01",
        gateway_capability_digest="d" * 64,
    )


def _receipt() -> GatewayPreflightReceiptV1:
    return GatewayPreflightReceiptV1(
        receipt_id="gateway-preflight-01",
        gateway_id="gpt2giga",
        route_id="codex-gpt2giga-gigachat-max",
        profile_digest="a" * 64,
        artifact_sha256="8" * 64,
        capability_revision="sha256:" + "b" * 64,
        models_revision="sha256:" + "e" * 64,
        loss_matrix_revision="sha256:" + "c" * 64,
        support_status=GatewaySupportStatus.TECHNICAL_PREVIEW,
        status=GatewayPreflightStatus.READY,
        reason_ids=("normalized_responses_parity_incomplete",),
        checked_at="2026-08-04T10:00:00+00:00",
    )


def test_gateway_contracts_round_trip_with_canonical_digests() -> None:
    profile = _profile()
    route = _route()
    overlay = _overlay()
    receipt = _receipt()

    assert gateway_profile_from_dict(gateway_profile_to_dict(profile)) == profile
    assert bridge_route_from_dict(bridge_route_to_dict(route)) == route
    assert launch_overlay_from_dict(launch_overlay_to_dict(overlay)) == overlay
    assert (
        gateway_preflight_receipt_from_dict(gateway_preflight_receipt_to_dict(receipt))
        == receipt
    )
    for value in (profile, route, overlay, receipt):
        assert len(gateway_contract_digest(value)) == 64


def test_gateway_profile_rejects_secret_bearing_or_unbound_identity() -> None:
    with pytest.raises(ValueError, match="credential-free"):
        replace(_profile(), base_url="https://user:secret@example.com")
    with pytest.raises(ValueError, match="artifact sha256"):
        replace(_profile(), artifact_sha256="latest")
    payload = gateway_profile_to_dict(_profile())
    payload["api_key"] = "plaintext"
    with pytest.raises(ValueError, match="unknown fields"):
        gateway_profile_from_dict(payload)


def test_route_support_truth_and_acknowledgement_fail_closed() -> None:
    with pytest.raises(ValueError, match="acknowledgement"):
        replace(
            _route(),
            support_status=GatewaySupportStatus.VENDOR_UNSUPPORTED,
            required_acknowledgement=None,
        )
    with pytest.raises(ValueError, match="reason id"):
        replace(
            _route(),
            support_status=GatewaySupportStatus.BLOCKED,
            reason_ids=(),
        )


def test_launch_overlay_never_projects_plaintext_secret_like_values() -> None:
    with pytest.raises(ValueError, match="must be redacted"):
        replace(
            _overlay(),
            redacted_env_delta=(("OPENAI_API_KEY", "plaintext-value"),),
        )
    payload = launch_overlay_to_dict(_overlay())
    assert payload["redacted_env_delta"]["OPENAI_API_KEY"] == "<secret-ref>"
    assert "plaintext" not in repr(payload)


def test_blocked_preflight_requires_reasons_and_cannot_be_ready() -> None:
    with pytest.raises(ValueError, match="requires a reason"):
        replace(_receipt(), status=GatewayPreflightStatus.BLOCKED, reason_ids=())
    with pytest.raises(ValueError, match="cannot have a ready"):
        replace(_receipt(), support_status=GatewaySupportStatus.BLOCKED)
