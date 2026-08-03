"""Agent-specific gateway overlays with immutable native homes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tomllib

from gigaloom.native.api import (
    BridgeRouteV1,
    GatewayDiscoveryResult,
    GatewayDiscoveryStatus,
    GatewayInjectionReason,
    GatewayInjectionStatus,
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
    GatewayRouteCatalogV1,
    GatewaySupportStatus,
    build_gateway_agent_injection,
)


NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)


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
        models_contract_revision="openai.models.v1",
        capabilities_contract_revision="gpt2giga.route-support-matrix.v1",
        auth_ref="secret-ref:gigachat",
        tls_policy_ref="tls-policy:loopback",
        profile_digest="a" * 64,
    )


def _route(
    agent_id: str = "codex",
    protocol: str = "openai_responses",
    *,
    status: GatewaySupportStatus = GatewaySupportStatus.TECHNICAL_PREVIEW,
) -> BridgeRouteV1:
    return BridgeRouteV1(
        route_id=f"{agent_id}-gpt2giga-gigachat-max",
        agent_id=agent_id,
        client_protocol=protocol,
        gateway_profile_id="gpt2giga",
        public_model_alias="GigaChat-2-Max",
        upstream_provider="gigachat",
        upstream_model="GigaChat-2-Max",
        capability_profile_revision="sha256:" + "b" * 64,
        loss_matrix_revision="sha256:" + "c" * 64,
        support_status=status,
        reason_ids=("normalized_responses_parity_incomplete",),
        evidence_ids=("COR-01-CODEX-RESPONSES-2026-08-03",),
        required_acknowledgement=(
            "acknowledge_vendor_unsupported"
            if status is GatewaySupportStatus.VENDOR_UNSUPPORTED
            else None
        ),
    )


def _discovery(route: BridgeRouteV1) -> GatewayDiscoveryResult:
    return GatewayDiscoveryResult(
        GatewayDiscoveryStatus.CURRENT,
        GatewayRouteCatalogV1(
            gateway_id="gpt2giga",
            profile_digest="a" * 64,
            models_revision="sha256:" + "d" * 64,
            capabilities_revision="sha256:" + "e" * 64,
            loss_matrix_revision="sha256:" + "c" * 64,
            routes=(route,),
            discovered_at=NOW.isoformat(),
            expires_at=(NOW + timedelta(minutes=1)).isoformat(),
            catalog_digest="f" * 64,
        ),
    )


def _preflight(route: BridgeRouteV1) -> GatewayPreflightReceiptV1:
    return GatewayPreflightReceiptV1(
        receipt_id="gateway-preflight-01",
        gateway_id="gpt2giga",
        route_id=route.route_id,
        profile_digest="a" * 64,
        artifact_sha256="8" * 64,
        capability_revision="sha256:" + "b" * 64,
        models_revision="sha256:" + "d" * 64,
        loss_matrix_revision="sha256:" + "c" * 64,
        support_status=route.support_status,
        status=GatewayPreflightStatus.READY,
        reason_ids=route.reason_ids,
        checked_at=NOW.isoformat(),
    )


def _call(route: BridgeRouteV1, root: Path, **kwargs):
    return build_gateway_agent_injection(
        route,
        _profile(),
        _discovery(route),
        _preflight(route),
        managed_root=root,
        process_lease_ref="native-process:gateway-01",
        clock=lambda: NOW,
        **kwargs,
    )


def test_codex_overlay_uses_responses_provider_in_isolated_codex_home(
    tmp_path: Path,
) -> None:
    route = _route()
    native_home = tmp_path / "real-home" / ".codex"
    native_home.mkdir(parents=True)
    sentinel = native_home / "config.toml"
    sentinel.write_text("user-owned = true\n", encoding="utf-8")

    result = _call(route, tmp_path / "managed", acknowledged=True)

    assert result.status is GatewayInjectionStatus.READY
    assert result.effective_support_status is GatewaySupportStatus.TECHNICAL_PREVIEW
    assert result.overlay is not None
    overlay_home = Path(result.overlay.managed_home)
    assert overlay_home.is_relative_to(tmp_path / "managed")
    config = tomllib.loads((overlay_home / "config.toml").read_text(encoding="utf-8"))
    assert config["model"] == "GigaChat-2-Max"
    provider = config["model_providers"]["gigaloom-gateway"]
    assert provider["base_url"] == "http://127.0.0.1:8090/v2"
    assert provider["wire_api"] == "responses"
    assert provider["env_key"] == "GPT2GIGA_API_KEY"
    assert sentinel.read_text(encoding="utf-8") == "user-owned = true\n"
    assert result.overlay.redacted_env_delta == (
        ("CODEX_HOME", str(overlay_home)),
        ("GPT2GIGA_API_KEY", "<secret-ref>"),
    )


def test_claude_route_is_downgraded_to_vendor_unsupported_until_acknowledged(
    tmp_path: Path,
) -> None:
    route = _route("claude", "anthropic_messages")
    native_home = tmp_path / "real-home" / ".claude"
    native_home.mkdir(parents=True)
    sentinel = native_home / "settings.json"
    sentinel.write_text("{}\n", encoding="utf-8")

    pending = _call(route, tmp_path / "managed")
    admitted = _call(route, tmp_path / "managed", acknowledged=True)

    assert pending.status is GatewayInjectionStatus.ACKNOWLEDGEMENT_REQUIRED
    assert pending.overlay is None
    assert pending.effective_support_status is GatewaySupportStatus.VENDOR_UNSUPPORTED
    assert admitted.status is GatewayInjectionStatus.READY
    assert admitted.command_args == ("--model", "GigaChat-2-Max")
    assert admitted.overlay is not None
    assert admitted.overlay.redacted_env_delta == (
        ("ANTHROPIC_AUTH_TOKEN", "<secret-ref>"),
        ("ANTHROPIC_BASE_URL", "http://127.0.0.1:8090/v1"),
        ("CLAUDE_CONFIG_DIR", admitted.overlay.managed_home),
    )
    assert sentinel.read_text(encoding="utf-8") == "{}\n"


def test_managed_acp_uses_advertised_model_selector(tmp_path: Path) -> None:
    route = _route("managed-agent", "acp")

    blocked = _call(route, tmp_path / "managed")
    ready = _call(
        route,
        tmp_path / "managed",
        acp_model_selector_id="model",
    )

    assert blocked.reason_ids == (
        GatewayInjectionReason.ACP_MODEL_SELECTOR_REQUIRED.value,
    )
    assert ready.status is GatewayInjectionStatus.READY
    assert ready.acp_config_selector == ("model", "GigaChat-2-Max")
    assert ready.overlay is not None
    selector = Path(ready.overlay.managed_home) / "selector.json"
    assert "GigaChat-2-Max" in selector.read_text(encoding="utf-8")


def test_gemini_native_has_visible_blocked_reason_and_writes_nothing(
    tmp_path: Path,
) -> None:
    route = _route("gemini", "gemini_protocol")
    managed = tmp_path / "managed"

    result = _call(route, managed)

    assert result.status is GatewayInjectionStatus.BLOCKED
    assert result.effective_support_status is GatewaySupportStatus.BLOCKED
    assert result.reason_ids == (
        GatewayInjectionReason.GEMINI_NATIVE_GATEWAY_UNSUPPORTED.value,
    )
    assert not managed.exists()


def test_stale_or_mismatched_preflight_never_materializes_overlay(
    tmp_path: Path,
) -> None:
    route = _route()
    discovery = _discovery(route)
    assert discovery.catalog is not None
    stale = GatewayDiscoveryResult(
        GatewayDiscoveryStatus.STALE,
        discovery.catalog,
        (),
    )
    root = tmp_path / "managed"

    stale_result = build_gateway_agent_injection(
        route,
        _profile(),
        stale,
        _preflight(route),
        managed_root=root,
        process_lease_ref="native-process:gateway-01",
        clock=lambda: NOW,
    )
    mismatch = build_gateway_agent_injection(
        route,
        _profile(),
        discovery,
        replace(_preflight(route), models_revision="sha256:" + "9" * 64),
        managed_root=root,
        process_lease_ref="native-process:gateway-01",
        clock=lambda: NOW,
    )

    assert stale_result.reason_ids == (GatewayInjectionReason.CAPABILITY_STALE.value,)
    assert mismatch.reason_ids == (
        GatewayInjectionReason.PREFLIGHT_BINDING_MISMATCH.value,
    )
    assert not root.exists()
