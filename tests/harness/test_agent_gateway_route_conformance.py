"""Hermetic conformance for pinned agent-to-gateway launch routes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, cast

import pytest

from gigaloom.cli_commands.gateway_application import GatewayLaunchApplication
from gigaloom.cli_commands.gateway_compatibility import (
    GatewayAgentCompatibilityDecisionV1,
)
from gigaloom.cli_commands.gateway_launch import (
    GatewayLaunchResolutionStatus,
    parse_gateway_launch_argv,
    resolve_gateway_launch_request,
)
from gigaloom.config import HarnessConfig
from gigaloom.native.api import (
    BridgeRouteV1,
    GatewayArtifactEvidenceV1,
    GatewayDiscoveryResult,
    GatewayDiscoveryStatus,
    GatewayInjectionReason,
    GatewayInjectionStatus,
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayRouteCatalogV1,
    GatewayRouteDiscovery,
    GatewaySidecarStatus,
    GatewaySupportStatus,
    ManagedGatewayLeaseV1,
    build_gateway_agent_injection,
)
from gigaloom.native.launch.gateway_profile import (
    GPT2GIGA_WHEEL_SHA256,
    reviewed_gpt2giga_profile,
)
from gigaloom.native.launch.gateway_discovery import (
    GatewayRouteRefusal,
    GatewayRouteResolver,
)


NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)


def _ready_compatibility(agent_id: str) -> GatewayAgentCompatibilityDecisionV1:
    return GatewayAgentCompatibilityDecisionV1(
        agent_id=agent_id,
        harness_id="codex-cli",
        status="ready",
        reason_id="gateway_agent_compatibility_admitted",
        expected_version_window="==0.146.0",
        observed_version="0.146.0",
    )


CORPUS_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "gateway"
    / "agent_gateway_launch_corpus_v1.json"
)


def _corpus() -> dict[str, Any]:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _case(case_id: str) -> dict[str, Any]:
    return next(case for case in _corpus()["cases"] if case["case_id"] == case_id)


def _route(case_id: str) -> BridgeRouteV1:
    case = _case(case_id)
    return BridgeRouteV1(
        route_id=case["route_id"],
        agent_id=(
            "acp" if case_id == "managed-acp-model-selector" else case["agent_id"]
        ),
        client_protocol=case["client_protocol"],
        gateway_profile_id="gpt2giga",
        public_model_alias=case["public_model_alias"],
        upstream_provider=case["upstream_provider"],
        upstream_model=case["public_model_alias"],
        capability_profile_revision=_corpus()["contracts"][
            "hermetic_capability_revision"
        ],
        loss_matrix_revision=_corpus()["contracts"]["loss_matrix_revision"],
        support_status=GatewaySupportStatus(case["gateway_support_status"]),
        reason_ids=tuple(case.get("reason_ids", ())),
        evidence_ids=tuple(case.get("evidence_ids", ())),
        required_acknowledgement=case.get("required_acknowledgement"),
    )


def _profile():
    return reviewed_gpt2giga_profile(
        base_url="http://127.0.0.1:8090",
        mode=GatewayMode.MANAGED,
    )


def _discovery(
    *routes: BridgeRouteV1,
    expires_at: datetime | None = None,
) -> GatewayDiscoveryResult:
    profile = _profile()
    return GatewayDiscoveryResult(
        GatewayDiscoveryStatus.CURRENT,
        GatewayRouteCatalogV1(
            gateway_id=profile.gateway_id,
            profile_digest=profile.profile_digest,
            models_revision="sha256:" + "d" * 64,
            capabilities_revision="sha256:" + "e" * 64,
            loss_matrix_revision=_corpus()["contracts"]["loss_matrix_revision"],
            routes=routes,
            discovered_at=NOW.isoformat(),
            expires_at=(expires_at or NOW + timedelta(minutes=1)).isoformat(),
            catalog_digest="f" * 64,
        ),
    )


def _preflight(route: BridgeRouteV1) -> GatewayPreflightReceiptV1:
    discovery = _discovery(route)
    assert discovery.catalog is not None
    return GatewayPreflightReceiptV1(
        receipt_id=f"preflight-{route.agent_id}",
        gateway_id="gpt2giga",
        route_id=route.route_id,
        profile_digest=_profile().profile_digest,
        artifact_sha256=GPT2GIGA_WHEEL_SHA256,
        capability_revision=route.capability_profile_revision,
        models_revision=discovery.catalog.models_revision,
        loss_matrix_revision=route.loss_matrix_revision,
        support_status=route.support_status,
        status=GatewayPreflightStatus.READY,
        reason_ids=route.reason_ids,
        checked_at=NOW.isoformat(),
    )


def _inject(
    route: BridgeRouteV1,
    root: Path,
    **kwargs: object,
):
    discovery = _discovery(route)
    agent_id = "managed-acp-agent" if route.agent_id == "acp" else route.agent_id
    resolved = GatewayRouteResolver(discovery).resolve(
        _profile(),
        requested_agent_kind=agent_id,
        requested_model_alias=route.public_model_alias,
        route_id=route.route_id,
    )
    assert not isinstance(resolved, GatewayRouteRefusal)
    return build_gateway_agent_injection(
        resolved,
        agent_id,
        _profile(),
        discovery,
        _preflight(route),
        managed_root=root,
        process_lease_ref="native-process:gateway-conformance",
        clock=lambda: NOW,
        **kwargs,
    )


def test_canonical_codex_command_resolves_one_pinned_responses_route() -> None:
    case = _case("codex-responses-gigachat")
    request = parse_gateway_launch_argv(case["selection"])
    route = _route(case["case_id"])

    assert request is not None
    result = resolve_gateway_launch_request(
        request,
        _discovery(route),
        profile=_profile(),
        interactive=False,
    )

    assert result.status is GatewayLaunchResolutionStatus.READY
    assert result.route == route
    assert result.candidate_route_ids == ("codex-gpt2giga-gigachat-2-max",)
    assert request.agent_args == ()


def test_cross_family_adapters_preserve_support_truth_and_exact_selectors(
    tmp_path: Path,
) -> None:
    codex = _inject(
        _route("codex-responses-gigachat"),
        tmp_path / "codex",
    )
    claude_route = _route("claude-anthropic-gigachat")
    claude_pending = _inject(claude_route, tmp_path / "claude")
    claude_ready = _inject(
        claude_route,
        tmp_path / "claude",
        acknowledged=True,
    )
    acp_route = _route("managed-acp-model-selector")
    acp = _inject(acp_route, tmp_path / "acp")
    gemini_root = tmp_path / "gemini"
    gemini = _inject(
        _route("gemini-native-custom-endpoint-blocked"),
        gemini_root,
    )

    assert codex.status is GatewayInjectionStatus.READY
    assert codex.effective_support_status is GatewaySupportStatus.TECHNICAL_PREVIEW
    assert codex.overlay is not None
    codex_config = Path(codex.overlay.managed_home) / "config.toml"
    assert 'wire_api = "responses"' in codex_config.read_text(encoding="utf-8")

    assert claude_pending.status is GatewayInjectionStatus.ACKNOWLEDGEMENT_REQUIRED
    assert claude_ready.status is GatewayInjectionStatus.READY
    assert (
        claude_ready.effective_support_status is GatewaySupportStatus.VENDOR_UNSUPPORTED
    )
    assert claude_ready.command_args == ("--model", "GigaChat-2-Max")

    assert acp.status is GatewayInjectionStatus.BLOCKED
    assert acp.reason_ids == (GatewayInjectionReason.AGENT_PROTOCOL_UNSUPPORTED.value,)

    assert gemini.status is GatewayInjectionStatus.BLOCKED
    assert gemini.effective_support_status is GatewaySupportStatus.BLOCKED
    assert gemini.reason_ids == ("gemini_custom_endpoint_unsupported",)
    assert not gemini_root.exists()


def test_blocked_anthropic_upstream_stops_before_provider_traffic() -> None:
    codex = _route("codex-responses-gigachat")
    blocked = BridgeRouteV1(
        route_id="codex-gpt2giga-anthropic-opus",
        agent_id="codex",
        client_protocol="openai_responses",
        gateway_profile_id="gpt2giga",
        public_model_alias="anthropic/opus",
        upstream_provider="anthropic",
        upstream_model="claude-opus",
        capability_profile_revision=codex.capability_profile_revision,
        loss_matrix_revision=codex.loss_matrix_revision,
        support_status=GatewaySupportStatus.BLOCKED,
        reason_ids=("provider_not_supported",),
        evidence_ids=(),
    )
    request = parse_gateway_launch_argv(
        ("--route", blocked.route_id, "codex", "provider-prompt")
    )
    provider_calls: list[object] = []

    assert request is not None
    result = resolve_gateway_launch_request(
        request,
        _discovery(blocked),
        profile=_profile(),
        interactive=False,
    )
    if result.ready:  # pragma: no cover - the provider must remain unreachable
        provider_calls.append(object())

    assert result.status is GatewayLaunchResolutionStatus.BLOCKED
    assert result.reason_ids == ("provider_not_supported",)
    assert provider_calls == []


class _Discovery:
    def __init__(self, result: GatewayDiscoveryResult) -> None:
        self.result = result
        self.calls = 0

    def discover(self, _profile: object, *, force_refresh: bool = False):
        assert force_refresh is True
        self.calls += 1
        return self.result


class _Sidecar:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.stop_calls = 0

    def ensure_started(
        self,
        profile: object,
        artifact: object,
        *,
        environment: Mapping[str, str],
        session_id: str,
        run_id: str,
    ) -> ManagedGatewayLeaseV1:
        del artifact
        assert environment["GIGACHAT_ACCESS_TOKEN"] == "fixture-token"
        assert (session_id, run_id) == ("gateway-launch", "gateway-gpt2giga")
        self.ensure_calls += 1
        return ManagedGatewayLeaseV1(
            gateway_id=cast(Any, profile).gateway_id,
            profile_digest=cast(Any, profile).profile_digest,
            status=GatewaySidecarStatus.STARTED,
            process_lease_ref="native-process:gateway-conformance",
            managed_root="/managed/gateway",
            startup_config_ref="managed-config:startup.json",
            readiness_confirmed=True,
        )

    def stop(self, profile: object) -> ManagedGatewayLeaseV1:
        self.stop_calls += 1
        return ManagedGatewayLeaseV1(
            gateway_id=cast(Any, profile).gateway_id,
            profile_digest=cast(Any, profile).profile_digest,
            status=GatewaySidecarStatus.STOPPED,
            process_lease_ref="native-process:gateway-conformance",
            managed_root="/managed/gateway",
            startup_config_ref="managed-config:startup.json",
            readiness_confirmed=False,
        )


def _artifact(tmp_path: Path) -> GatewayArtifactEvidenceV1:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "gpt2giga"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    return GatewayArtifactEvidenceV1(
        distribution="gpt2giga",
        version="0.3.0",
        artifact_sha256=GPT2GIGA_WHEEL_SHA256,
        executable_path=str(executable),
        source="locked-registry:pypi/gpt2giga==0.3.0",
        verified=True,
    )


def _application(
    tmp_path: Path,
    discovery_result: GatewayDiscoveryResult,
) -> tuple[GatewayLaunchApplication, _Discovery, _Sidecar]:
    discovery = _Discovery(discovery_result)
    sidecar = _Sidecar()
    application = GatewayLaunchApplication(
        config=HarnessConfig(data_dir=str(tmp_path / "state")),
        profile=_profile(),
        discovery=cast(GatewayRouteDiscovery, discovery),
        artifact_resolver=lambda _profile: _artifact(tmp_path),
        managed_root=tmp_path / "managed",
        gateway_api_key="fixture-gateway-key",
        compatibility_resolver=_ready_compatibility,
        sidecar=sidecar,
        startup_inspector=None,
        clock=lambda: NOW,
    )
    return application, discovery, sidecar


def _json_request():
    selection = list(_case("codex-responses-gigachat")["selection"])
    selection.insert(-1, "--json")
    request = parse_gateway_launch_argv(selection)
    assert request is not None
    return request


def test_auth_unavailable_and_expired_capability_fail_without_native_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in tuple(__import__("os").environ):
        if name.startswith("GIGACHAT_"):
            monkeypatch.delenv(name, raising=False)
    route = _route("codex-responses-gigachat")
    unavailable, unavailable_discovery, unavailable_sidecar = _application(
        tmp_path / "unavailable",
        _discovery(route),
    )

    launched: list[object] = []
    assert (
        unavailable.run(
            _json_request(),
            native_launcher=lambda *_args: launched.append(object()) or 0,
        )
        == 2
    )
    unavailable_payload = json.loads(capsys.readouterr().out)
    assert unavailable_payload["reason_ids"] == [
        "gateway_upstream_credentials_unavailable"
    ]
    assert unavailable_discovery.calls == 0
    assert unavailable_sidecar.ensure_calls == 0

    monkeypatch.setenv("GIGACHAT_ACCESS_TOKEN", "fixture-token")
    expired, expired_discovery, expired_sidecar = _application(
        tmp_path / "expired",
        _discovery(route, expires_at=NOW - timedelta(seconds=1)),
    )
    assert (
        expired.run(
            _json_request(),
            native_launcher=lambda *_args: launched.append(object()) or 0,
        )
        == 2
    )
    expired_payload = json.loads(capsys.readouterr().out)
    assert expired_payload["reason_ids"] == ["capability_stale"]
    assert expired_discovery.calls == 1
    assert expired_sidecar.ensure_calls == 1
    assert expired_sidecar.stop_calls == 1
    assert launched == []


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), BrokenPipeError()])
def test_cancellation_and_stream_disconnect_release_started_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    monkeypatch.setenv("GIGACHAT_ACCESS_TOKEN", "fixture-token")
    route = _route("codex-responses-gigachat")
    application, discovery, sidecar = _application(tmp_path, _discovery(route))

    def fail_native(
        _argv: tuple[str, ...],
        _environment: Mapping[str, str],
    ) -> int:
        raise failure

    with pytest.raises(type(failure)):
        application.run(_json_request(), native_launcher=fail_native)

    assert discovery.calls == 1
    assert sidecar.ensure_calls == 1
    assert sidecar.stop_calls == 1
