"""Strict decoder for version-1 declarative Agent Profile manifests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gigaloom.harnesses.agent_profiles.models import (
    AgentProfileSource,
    AgentProfileSourceKind,
    AgentProfileTrustClass,
    AgentProfileV1,
    AuthOwner,
    CompatibilityProfileRef,
    ExecutableCommandRef,
    StructuredAgentRouteRef,
    VersionPolicy,
    VersionPolicyKind,
)
from gigaloom.native.api import (
    NativeAgentLaunchSpec,
    NativeIntentMatcher,
    NativeIntentMatcherKind,
)


def decode_agent_profile_manifest(
    document: Mapping[str, Any],
    *,
    manifest_digest: str,
) -> AgentProfileV1:
    """Decode one strict manifest without granting discovery or launch authority."""
    root = _mapping(document, field_name="agent profile")
    _exact_keys(
        root,
        {
            "schema_version",
            "agent_id",
            "display_name",
            "aliases",
            "profile_version",
            "auth_owner",
            "platform_support",
            "source",
            "native",
            "compatibility_profiles",
            "structured_routes",
        },
        field_name="agent profile",
    )
    source_document = _mapping(root["source"], field_name="agent profile source")
    _exact_keys(
        source_document,
        {"kind", "origin", "revision", "trust_class", "reviewed"},
        field_name="agent profile source",
    )
    source = AgentProfileSource(
        kind=_enum_value(
            AgentProfileSourceKind,
            source_document["kind"],
            field_name="agent profile source kind",
        ),
        origin=_string(source_document["origin"], field_name="source origin"),
        revision=_string(source_document["revision"], field_name="source revision"),
        digest=manifest_digest,
        trust_class=_enum_value(
            AgentProfileTrustClass,
            source_document["trust_class"],
            field_name="agent profile trust class",
        ),
        reviewed=_boolean(source_document["reviewed"], field_name="source reviewed"),
    )
    compatibility_profiles = tuple(
        _decode_compatibility_profile(item)
        for item in _sequence(
            root["compatibility_profiles"],
            field_name="compatibility profiles",
        )
    )
    routes = tuple(
        _decode_structured_route(item)
        for item in _sequence(
            root["structured_routes"],
            field_name="structured routes",
        )
    )
    native_document = root["native"]
    native = (
        None
        if native_document is None
        else _decode_native_spec(
            _mapping(native_document, field_name="native launch spec")
        )
    )
    return AgentProfileV1(
        schema_version=_integer(root["schema_version"], field_name="schema_version"),
        agent_id=_string(root["agent_id"], field_name="agent_id"),
        display_name=_string(root["display_name"], field_name="display_name"),
        aliases=_string_tuple(root["aliases"], field_name="aliases"),
        profile_version=_string(
            root["profile_version"],
            field_name="profile_version",
        ),
        source=source,
        native=native,
        structured_routes=routes,
        auth_owner=_enum_value(
            AuthOwner,
            root["auth_owner"],
            field_name="auth_owner",
        ),
        platform_support=_string_tuple(
            root["platform_support"],
            field_name="platform_support",
        ),
        compatibility_profiles=compatibility_profiles,
        profile_digest=manifest_digest,
    )


def _decode_native_spec(document: Mapping[str, Any]) -> NativeAgentLaunchSpec:
    _exact_keys(
        document,
        {
            "executable_names",
            "provider_home_markers",
            "provider_config_markers",
            "version_probe",
            "supports_managed_terminal",
            "interactive_matchers",
            "metadata_matchers",
            "headless_matchers",
        },
        field_name="native launch spec",
    )
    probe = document["version_probe"]
    return NativeAgentLaunchSpec(
        executable_names=_string_tuple(
            document["executable_names"],
            field_name="native executable names",
        ),
        provider_home_markers=_string_tuple(
            document["provider_home_markers"],
            field_name="provider home markers",
        ),
        provider_config_markers=_string_tuple(
            document["provider_config_markers"],
            field_name="provider config markers",
        ),
        version_probe=(
            None
            if probe is None
            else _string_tuple(probe, field_name="native version probe")
        ),
        interactive_matchers=_decode_matchers(
            document["interactive_matchers"],
            field_name="interactive matchers",
        ),
        metadata_matchers=_decode_matchers(
            document["metadata_matchers"],
            field_name="metadata matchers",
        ),
        headless_matchers=_decode_matchers(
            document["headless_matchers"],
            field_name="headless matchers",
        ),
        supports_managed_terminal=_boolean(
            document["supports_managed_terminal"],
            field_name="supports_managed_terminal",
        ),
    )


def _decode_matchers(value: Any, *, field_name: str) -> tuple[NativeIntentMatcher, ...]:
    matchers: list[NativeIntentMatcher] = []
    for item in _sequence(value, field_name=field_name):
        document = _mapping(item, field_name=field_name)
        _exact_keys(
            document,
            {"matcher_id", "kind", "tokens", "precedence"},
            field_name=field_name,
        )
        matchers.append(
            NativeIntentMatcher(
                matcher_id=_string(
                    document["matcher_id"],
                    field_name="matcher_id",
                ),
                kind=_enum_value(
                    NativeIntentMatcherKind,
                    document["kind"],
                    field_name="matcher kind",
                ),
                tokens=_string_tuple(document["tokens"], field_name="matcher tokens"),
                precedence=_integer(
                    document["precedence"],
                    field_name="matcher precedence",
                ),
            )
        )
    return tuple(matchers)


def _decode_compatibility_profile(value: Any) -> CompatibilityProfileRef:
    document = _mapping(value, field_name="compatibility profile")
    _exact_keys(
        document,
        {"compatibility_profile_id", "revision", "evidence_digest"},
        field_name="compatibility profile",
    )
    return CompatibilityProfileRef(
        compatibility_profile_id=_string(
            document["compatibility_profile_id"],
            field_name="compatibility_profile_id",
        ),
        revision=_string(document["revision"], field_name="compatibility revision"),
        evidence_digest=_string(
            document["evidence_digest"],
            field_name="compatibility evidence_digest",
        ),
    )


def _decode_structured_route(value: Any) -> StructuredAgentRouteRef:
    document = _mapping(value, field_name="structured route")
    _exact_keys(
        document,
        {
            "route_id",
            "harness_id",
            "transport_kind",
            "compatibility_profile_id",
            "command_ref",
            "capability_requirements",
            "version_policy",
        },
        field_name="structured route",
    )
    command_document = document["command_ref"]
    command_ref = None
    if command_document is not None:
        command = _mapping(command_document, field_name="structured command ref")
        _exact_keys(
            command,
            {"executable_name", "arguments"},
            field_name="structured command ref",
        )
        command_ref = ExecutableCommandRef(
            executable_name=_string(
                command["executable_name"],
                field_name="structured executable name",
            ),
            arguments=_string_tuple(
                command["arguments"],
                field_name="structured command arguments",
            ),
        )
    policy_document = _mapping(
        document["version_policy"],
        field_name="version policy",
    )
    _required_keys(
        policy_document,
        required={"kind"},
        allowed={"kind", "exact_version", "minimum", "maximum_exclusive"},
        field_name="version policy",
    )
    return StructuredAgentRouteRef(
        route_id=_string(document["route_id"], field_name="route_id"),
        harness_id=_string(document["harness_id"], field_name="harness_id"),
        transport_kind=_string(
            document["transport_kind"],
            field_name="transport_kind",
        ),
        compatibility_profile_id=_string(
            document["compatibility_profile_id"],
            field_name="compatibility_profile_id",
        ),
        command_ref=command_ref,
        capability_requirements=_string_tuple(
            document["capability_requirements"],
            field_name="capability_requirements",
        ),
        version_policy=VersionPolicy(
            kind=_enum_value(
                VersionPolicyKind,
                policy_document["kind"],
                field_name="version policy kind",
            ),
            exact_version=_optional_string(
                policy_document.get("exact_version"),
                field_name="exact_version",
            ),
            minimum=_optional_string(
                policy_document.get("minimum"),
                field_name="minimum",
            ),
            maximum_exclusive=_optional_string(
                policy_document.get("maximum_exclusive"),
                field_name="maximum_exclusive",
            ),
        ),
    )


def _exact_keys(
    document: Mapping[str, Any],
    expected: set[str],
    *,
    field_name: str,
) -> None:
    actual = set(document)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"{field_name} keys are invalid; missing={missing}, unknown={unknown}"
        )


def _required_keys(
    document: Mapping[str, Any],
    *,
    required: set[str],
    allowed: set[str],
    field_name: str,
) -> None:
    actual = set(document)
    missing = sorted(required - actual)
    unknown = sorted(actual - allowed)
    if missing or unknown:
        raise ValueError(
            f"{field_name} keys are invalid; missing={missing}, unknown={unknown}"
        )


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return tuple(value)


def _string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name=field_name)


def _string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    items = _sequence(value, field_name=field_name)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{field_name} must contain strings")
    return tuple(items)


def _integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _enum_value(enum_type: type[Any], value: Any, *, field_name: str) -> Any:
    text = _string(value, field_name=field_name)
    try:
        return enum_type(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} is unsupported") from exc
