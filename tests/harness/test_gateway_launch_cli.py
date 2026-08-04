"""Deterministic CLI boundary for explicit gateway route launches."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from gigaloom.cli_commands.gateway_launch import (
    GatewayLaunchParseCode,
    GatewayLaunchParseError,
    GatewayLaunchResolutionStatus,
    gateway_launch_resolution_to_dict,
    parse_gateway_launch_argv,
    resolve_gateway_launch_request,
)
from gigaloom.native.api import (
    BridgeRouteV1,
    GatewayDiscoveryReason,
    GatewayDiscoveryResult,
    GatewayDiscoveryStatus,
    GatewayMode,
    GatewayProfileV1,
    GatewayRouteCatalogV1,
    GatewaySupportStatus,
)


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


def _route(
    route_id: str = "codex-gpt2giga-gigachat-max",
    *,
    support: GatewaySupportStatus = GatewaySupportStatus.TECHNICAL_PREVIEW,
    acknowledgement: str | None = None,
) -> BridgeRouteV1:
    return BridgeRouteV1(
        route_id=route_id,
        agent_id="codex",
        client_protocol="openai_responses",
        gateway_profile_id="gpt2giga",
        public_model_alias="GigaChat-2-Max",
        upstream_provider="gigachat",
        upstream_model="GigaChat-2-Max",
        capability_profile_revision="sha256:" + "b" * 64,
        loss_matrix_revision="sha256:" + "c" * 64,
        support_status=support,
        reason_ids=("normalized_responses_parity_incomplete",),
        evidence_ids=("COR-01-CODEX-RESPONSES-2026-08-03",),
        required_acknowledgement=acknowledgement,
    )


def _discovery(*routes: BridgeRouteV1) -> GatewayDiscoveryResult:
    now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    return GatewayDiscoveryResult(
        GatewayDiscoveryStatus.CURRENT,
        GatewayRouteCatalogV1(
            gateway_id="gpt2giga",
            profile_digest="a" * 64,
            models_revision="sha256:" + "d" * 64,
            capabilities_revision="sha256:" + "e" * 64,
            loss_matrix_revision="sha256:" + "c" * 64,
            routes=routes,
            discovered_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=1)).isoformat(),
            catalog_digest="f" * 64,
        ),
    )


def test_global_options_stop_at_agent_and_preserve_opaque_suffix() -> None:
    request = parse_gateway_launch_argv(
        (
            "--with",
            "gpt2giga",
            "--model=GigaChat-2-Max",
            "--dry-run",
            "--json",
            "codex",
            "--model",
            "native-model",
            "",
            "Привет\nмир",
        )
    )

    assert request is not None
    assert request.gateway_id == "gpt2giga"
    assert request.public_model_alias == "GigaChat-2-Max"
    assert request.route_id is None
    assert request.agent_id == "codex"
    assert request.agent_args == ("--model", "native-model", "", "Привет\nмир")
    assert request.dry_run is True
    assert request.json_output is True


@pytest.mark.parametrize(
    "argv",
    (
        ("codex", "--model", "native-model"),
        ("claude",),
        ("route", "list"),
        ("--help",),
        ("--non-interactive", "doctor"),
    ),
)
def test_existing_commands_are_untouched_without_a_global_selector(argv) -> None:
    assert parse_gateway_launch_argv(argv) is None


@pytest.mark.parametrize(
    ("argv", "code"),
    (
        (
            ("--route", "r1", "--with", "g", "--model", "m", "codex"),
            GatewayLaunchParseCode.MIXED_SELECTORS,
        ),
        (
            ("--with", "g", "codex"),
            GatewayLaunchParseCode.INCOMPLETE_CONVENIENCE_SELECTOR,
        ),
        (
            ("--model", "m", "codex"),
            GatewayLaunchParseCode.INCOMPLETE_CONVENIENCE_SELECTOR,
        ),
        (("--route", "codex"), GatewayLaunchParseCode.MISSING_AGENT),
        (
            ("--route", "r1", "--future", "codex"),
            GatewayLaunchParseCode.UNKNOWN_GLOBAL_OPTION,
        ),
        (
            ("--route", "r1", "--route", "r2", "codex"),
            GatewayLaunchParseCode.DUPLICATE_OPTION,
        ),
    ),
)
def test_invalid_or_ambiguous_global_grammar_fails_closed(argv, code) -> None:
    with pytest.raises(GatewayLaunchParseError) as raised:
        parse_gateway_launch_argv(argv)
    assert raised.value.code is code


def test_exact_route_and_convenience_route_resolve_without_fallback() -> None:
    route = _route()
    exact = parse_gateway_launch_argv(("--route", route.route_id, "codex"))
    convenience = parse_gateway_launch_argv(
        ("--with", "gpt2giga", "--model", "GigaChat-2-Max", "codex")
    )
    assert exact is not None and convenience is not None

    exact_result = resolve_gateway_launch_request(
        exact,
        _discovery(route),
        profile=_profile(),
        interactive=False,
    )
    convenience_result = resolve_gateway_launch_request(
        convenience,
        _discovery(route),
        profile=_profile(),
        interactive=False,
    )

    assert exact_result.status is GatewayLaunchResolutionStatus.READY
    assert convenience_result.status is GatewayLaunchResolutionStatus.READY
    assert exact_result.route == convenience_result.route == route
    assert exact_result.resolved_route == convenience_result.resolved_route
    assert exact_result.resolved_route is not None
    assert exact_result.resolved_route.provider_protocol == "openai_responses"


def test_non_tty_ambiguity_is_hard_error_and_tty_picker_is_bounded() -> None:
    first = _route("codex-gpt2giga-gigachat-max-a")
    second = _route("codex-gpt2giga-gigachat-max-b")
    request = parse_gateway_launch_argv(
        ("--with", "gpt2giga", "--model", "GigaChat-2-Max", "codex")
    )
    assert request is not None

    non_tty = resolve_gateway_launch_request(
        request,
        _discovery(first, second),
        profile=_profile(),
        interactive=False,
    )
    tty = resolve_gateway_launch_request(
        request,
        _discovery(first, second),
        profile=_profile(),
        interactive=True,
        picker=lambda candidates: candidates[1],
    )

    assert non_tty.status is GatewayLaunchResolutionStatus.AMBIGUOUS
    assert non_tty.route is None
    assert tty.status is GatewayLaunchResolutionStatus.READY
    assert tty.route == second


def test_stale_unknown_blocked_and_acknowledgement_states_are_explicit() -> None:
    request = parse_gateway_launch_argv(
        ("--route", "codex-gpt2giga-gigachat-max", "codex")
    )
    assert request is not None
    current = _discovery(_route())
    stale = GatewayDiscoveryResult(
        GatewayDiscoveryStatus.STALE,
        current.catalog,
        (GatewayDiscoveryReason.MODELS_UNAVAILABLE,),
    )
    unknown = GatewayDiscoveryResult(
        GatewayDiscoveryStatus.UNKNOWN,
        None,
        (GatewayDiscoveryReason.CONTRACT_REVISION_MISMATCH,),
    )

    assert (
        resolve_gateway_launch_request(
            request, stale, profile=_profile(), interactive=False
        ).status
        is GatewayLaunchResolutionStatus.CAPABILITY_STALE
    )
    assert (
        resolve_gateway_launch_request(
            request, unknown, profile=_profile(), interactive=False
        ).status
        is GatewayLaunchResolutionStatus.CAPABILITY_UNKNOWN
    )
    assert (
        resolve_gateway_launch_request(
            request,
            _discovery(replace(_route(), support_status=GatewaySupportStatus.BLOCKED)),
            profile=_profile(),
            interactive=False,
        ).status
        is GatewayLaunchResolutionStatus.BLOCKED
    )
    assert (
        resolve_gateway_launch_request(
            request,
            _discovery(
                replace(
                    _route(),
                    support_status=GatewaySupportStatus.VENDOR_UNSUPPORTED,
                    required_acknowledgement="acknowledge_vendor_unsupported",
                )
            ),
            profile=_profile(),
            interactive=False,
        ).status
        is GatewayLaunchResolutionStatus.ACKNOWLEDGEMENT_REQUIRED
    )


def test_dry_run_json_is_redacted_and_never_contains_native_argument_values() -> None:
    request = parse_gateway_launch_argv(
        (
            "--route",
            "codex-gpt2giga-gigachat-max",
            "--dry-run",
            "--json",
            "codex",
            "secret prompt",
            "--api-key=plaintext",
        )
    )
    assert request is not None
    result = resolve_gateway_launch_request(
        request,
        _discovery(_route()),
        profile=_profile(),
        interactive=False,
    )

    payload = gateway_launch_resolution_to_dict(result)
    assert payload["native_args"] == {
        "count": 2,
        "opaque": True,
        "values_included": False,
    }
    assert payload["provider_traffic"] is False
    assert payload["process_spawn"] is False
    assert "secret prompt" not in repr(payload)
    assert "plaintext" not in repr(payload)
