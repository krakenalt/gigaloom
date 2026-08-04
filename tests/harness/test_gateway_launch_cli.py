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
    GatewayRouteCatalogV1,
    GatewaySupportStatus,
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
        interactive=False,
    )
    convenience_result = resolve_gateway_launch_request(
        convenience,
        _discovery(route),
        interactive=False,
    )

    assert exact_result.status is GatewayLaunchResolutionStatus.READY
    assert convenience_result.status is GatewayLaunchResolutionStatus.READY
    assert exact_result.route == convenience_result.route == route


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
        interactive=False,
    )
    tty = resolve_gateway_launch_request(
        request,
        _discovery(first, second),
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
        resolve_gateway_launch_request(request, stale, interactive=False).status
        is GatewayLaunchResolutionStatus.CAPABILITY_STALE
    )
    assert (
        resolve_gateway_launch_request(request, unknown, interactive=False).status
        is GatewayLaunchResolutionStatus.CAPABILITY_UNKNOWN
    )
    assert (
        resolve_gateway_launch_request(
            request,
            _discovery(replace(_route(), support_status=GatewaySupportStatus.BLOCKED)),
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
