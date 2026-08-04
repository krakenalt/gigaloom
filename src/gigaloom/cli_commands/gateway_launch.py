"""Deterministic global option boundary for reviewed gateway launches."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from gigaloom.native.api import (
    BridgeRouteV1,
    GatewayDiscoveryResult,
    GatewayProfileV1,
    GatewaySupportStatus,
    ResolvedGatewayRoute,
)
from gigaloom.native.launch.gateway_discovery import (
    GatewayRouteRefusal,
    GatewayRouteResolver,
)


_VALUE_OPTIONS = {"--route", "--with", "--model"}
_FLAG_OPTIONS = {"--dry-run", "--json"}


class GatewayLaunchParseCode(str, Enum):
    """Stable parsing failures before agent or provider execution."""

    MISSING_VALUE = "missing_value"
    DUPLICATE_OPTION = "duplicate_option"
    MIXED_SELECTORS = "mixed_selectors"
    INCOMPLETE_CONVENIENCE_SELECTOR = "incomplete_convenience_selector"
    MISSING_AGENT = "missing_agent"
    UNKNOWN_GLOBAL_OPTION = "unknown_global_option"


class GatewayLaunchParseError(ValueError):
    """Content-free global gateway option error."""

    def __init__(self, code: GatewayLaunchParseCode):
        super().__init__(code.value)
        self.code = code


class GatewayLaunchResolutionStatus(str, Enum):
    """Fail-closed route resolution outcomes."""

    READY = "ready"
    ACKNOWLEDGEMENT_REQUIRED = "acknowledgement_required"
    BLOCKED = "blocked"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    CAPABILITY_STALE = "capability_stale"
    CAPABILITY_UNKNOWN = "capability_unknown"


@dataclass(frozen=True)
class GatewayLaunchRequestV1:
    """Transient global selection with an opaque native-agent suffix."""

    agent_id: str
    agent_args: tuple[str, ...]
    route_id: str | None
    gateway_id: str | None
    public_model_alias: str | None
    dry_run: bool
    json_output: bool

    def __post_init__(self) -> None:
        if (
            not self.agent_id
            or "\x00" in self.agent_id
            or self.agent_id.startswith("-")
        ):
            raise ValueError("gateway launch agent id is invalid")
        if any("\x00" in item for item in self.agent_args):
            raise ValueError("gateway launch agent arguments are invalid")
        exact = self.route_id is not None
        convenience = self.gateway_id is not None or self.public_model_alias is not None
        if exact == convenience:
            raise ValueError("gateway launch requires exactly one selector form")
        if convenience and (self.gateway_id is None or self.public_model_alias is None):
            raise ValueError("gateway launch convenience selector is incomplete")


@dataclass(frozen=True)
class GatewayLaunchResolutionV1:
    """Selected route or typed refusal with no process-launch authority."""

    status: GatewayLaunchResolutionStatus
    request: GatewayLaunchRequestV1
    route: BridgeRouteV1 | None
    resolved_route: ResolvedGatewayRoute | None
    candidate_route_ids: tuple[str, ...]
    reason_ids: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Return whether the exact route may proceed to preflight."""
        return (
            self.status is GatewayLaunchResolutionStatus.READY
            and self.route is not None
            and self.resolved_route is not None
        )


GatewayRoutePicker = Callable[[tuple[str, ...]], str | None]


def parse_gateway_launch_argv(
    argv: Sequence[str],
) -> GatewayLaunchRequestV1 | None:
    """Parse route options only before the first agent token.

    Returns ``None`` when no gateway selector is present so all pre-0.9 command
    and native-agent forms remain owned by the existing root dispatcher.
    """
    arguments = tuple(argv)
    if not arguments or not arguments[0].startswith("-"):
        return None
    values: dict[str, str] = {}
    flags: set[str] = set()
    index = 0
    saw_selector = False
    agent_id: str | None = None
    while index < len(arguments):
        token = arguments[index]
        if not token.startswith("-"):
            agent_id = token
            index += 1
            break
        option, inline_value = _split_option(token)
        if option in _VALUE_OPTIONS:
            saw_selector = True
            if option in values:
                raise GatewayLaunchParseError(GatewayLaunchParseCode.DUPLICATE_OPTION)
            if inline_value is None:
                index += 1
                if index >= len(arguments) or arguments[index].startswith("-"):
                    raise GatewayLaunchParseError(GatewayLaunchParseCode.MISSING_VALUE)
                inline_value = arguments[index]
            if not inline_value or "\x00" in inline_value:
                raise GatewayLaunchParseError(GatewayLaunchParseCode.MISSING_VALUE)
            values[option] = inline_value
        elif option in _FLAG_OPTIONS and inline_value is None:
            flags.add(option)
        else:
            if not saw_selector:
                return None
            raise GatewayLaunchParseError(GatewayLaunchParseCode.UNKNOWN_GLOBAL_OPTION)
        index += 1
    if not saw_selector:
        return None
    if agent_id is None:
        raise GatewayLaunchParseError(GatewayLaunchParseCode.MISSING_AGENT)
    route_id = values.get("--route")
    gateway_id = values.get("--with")
    model = values.get("--model")
    if route_id is not None and (gateway_id is not None or model is not None):
        raise GatewayLaunchParseError(GatewayLaunchParseCode.MIXED_SELECTORS)
    if route_id is None and (gateway_id is None or model is None):
        raise GatewayLaunchParseError(
            GatewayLaunchParseCode.INCOMPLETE_CONVENIENCE_SELECTOR
        )
    return GatewayLaunchRequestV1(
        agent_id=agent_id,
        agent_args=arguments[index:],
        route_id=route_id,
        gateway_id=gateway_id,
        public_model_alias=model,
        dry_run="--dry-run" in flags,
        json_output="--json" in flags,
    )


def resolve_gateway_launch_request(
    request: GatewayLaunchRequestV1,
    discovery: GatewayDiscoveryResult,
    *,
    profile: GatewayProfileV1,
    interactive: bool,
    picker: GatewayRoutePicker | None = None,
) -> GatewayLaunchResolutionV1:
    """Resolve one immutable route without fallback or provider traffic."""
    if request.gateway_id is not None and request.gateway_id != profile.gateway_id:
        return _refusal(
            GatewayLaunchResolutionStatus.NOT_FOUND,
            request,
            ("route_not_found",),
        )
    resolver = GatewayRouteResolver(discovery)
    resolved = resolver.resolve(
        profile,
        requested_agent_kind=request.agent_id,
        requested_model_alias=request.public_model_alias,
        route_id=request.route_id,
    )
    candidate_ids: tuple[str, ...] = ()
    if isinstance(resolved, GatewayRouteRefusal):
        candidate_ids = resolved.candidate_route_ids
    if isinstance(resolved, GatewayRouteRefusal) and resolved.status == "ambiguous":
        if not interactive or picker is None:
            return GatewayLaunchResolutionV1(
                GatewayLaunchResolutionStatus.AMBIGUOUS,
                request,
                None,
                None,
                candidate_ids,
                resolved.reason_ids,
            )
        selected_id = picker(candidate_ids)
        if selected_id not in candidate_ids:
            return GatewayLaunchResolutionV1(
                GatewayLaunchResolutionStatus.AMBIGUOUS,
                request,
                None,
                None,
                candidate_ids,
                ("route_picker_did_not_select_candidate",),
            )
        resolved = resolver.resolve(
            profile,
            requested_agent_kind=request.agent_id,
            requested_model_alias=request.public_model_alias,
            route_id=selected_id,
        )
    if isinstance(resolved, GatewayRouteRefusal):
        return GatewayLaunchResolutionV1(
            GatewayLaunchResolutionStatus(resolved.status),
            request,
            None,
            None,
            resolved.candidate_route_ids,
            resolved.reason_ids,
        )
    assert discovery.catalog is not None
    selected = next(
        route
        for route in discovery.catalog.routes
        if route.route_id == resolved.route_id
    )
    if resolved.support_status == GatewaySupportStatus.BLOCKED.value:
        status = GatewayLaunchResolutionStatus.BLOCKED
    elif selected.required_acknowledgement is not None:
        status = GatewayLaunchResolutionStatus.ACKNOWLEDGEMENT_REQUIRED
    else:
        status = GatewayLaunchResolutionStatus.READY
    return GatewayLaunchResolutionV1(
        status,
        request,
        selected,
        resolved,
        candidate_ids or (selected.route_id,),
        resolved.reason_ids,
    )


def gateway_launch_resolution_to_dict(
    value: GatewayLaunchResolutionV1,
) -> dict[str, Any]:
    """Serialize a content-free dry-run plan without native argument values."""
    route = value.route
    return {
        "schema_version": 1,
        "status": value.status.value,
        "agent_id": value.request.agent_id,
        "route_id": route.route_id if route is not None else value.request.route_id,
        "gateway_id": (
            route.gateway_profile_id if route is not None else value.request.gateway_id
        ),
        "public_model_alias": (
            route.public_model_alias
            if route is not None
            else value.request.public_model_alias
        ),
        "client_protocol": route.client_protocol if route is not None else None,
        "support_status": (route.support_status.value if route is not None else None),
        "required_acknowledgement": (
            route.required_acknowledgement if route is not None else None
        ),
        "reason_ids": list(value.reason_ids),
        "candidate_route_ids": list(value.candidate_route_ids),
        "native_args": {
            "count": len(value.request.agent_args),
            "opaque": True,
            "values_included": False,
        },
        "dry_run": value.request.dry_run,
        "provider_traffic": False,
        "process_spawn": False,
    }


def _split_option(token: str) -> tuple[str, str | None]:
    option, separator, value = token.partition("=")
    return option, value if separator else None


def _refusal(
    status: GatewayLaunchResolutionStatus,
    request: GatewayLaunchRequestV1,
    reasons: tuple[str, ...],
) -> GatewayLaunchResolutionV1:
    return GatewayLaunchResolutionV1(status, request, None, None, (), reasons)
