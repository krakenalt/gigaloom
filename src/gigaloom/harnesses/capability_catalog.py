"""Content-free structured-route capability catalog read model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re

from gigaloom.harnesses.acp.contracts import AcpCapabilitySnapshotV1
from gigaloom.harnesses.agent_profiles.models import AgentProfileV1


CAPABILITY_CATALOG_SCHEMA_VERSION = 1
UNKNOWN_CAPABILITY_SNAPSHOT_DIGEST = sha256(
    b"gigaloom.capability_snapshot.unknown.v1"
).hexdigest()
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


class CapabilityCatalogFactState(str, Enum):
    """Honest availability state for one structured-route capability."""

    READY = "ready"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CapabilitySnapshotState(str, Enum):
    """Freshness state for capability evidence bound into the catalog."""

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CapabilityCatalogFactV1:
    """One capability fact projected without provider response content."""

    capability_id: str
    state: CapabilityCatalogFactState

    def __post_init__(self) -> None:
        _validate_identity(self.capability_id, "capability id")
        if not isinstance(self.state, CapabilityCatalogFactState):
            raise ValueError("capability fact state is invalid")


@dataclass(frozen=True)
class StructuredRouteDescriptorV1:
    """One Agent Profile route bound to its exact profile evidence."""

    agent_id: str
    profile_version: str
    profile_digest: str
    route_id: str
    harness_id: str
    transport_kind: str
    compatibility_profile_id: str
    compatibility_profile_digest: str
    command: tuple[str, ...] | None
    capability_requirements: tuple[str, ...]
    platform_support: tuple[str, ...]
    version_policy_kind: str
    exact_version: str | None
    minimum_version: str | None
    maximum_exclusive_version: str | None
    source_trust_class: str
    source_reviewed: bool

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.agent_id, "agent id"),
            (self.profile_version, "profile version"),
            (self.route_id, "route id"),
            (self.harness_id, "harness id"),
            (self.transport_kind, "transport kind"),
            (self.compatibility_profile_id, "compatibility profile id"),
            (self.version_policy_kind, "version policy kind"),
            (self.source_trust_class, "source trust class"),
        ):
            _validate_identity(value, field_name)
        _validate_digest(self.profile_digest, "profile digest")
        _validate_digest(
            self.compatibility_profile_digest,
            "compatibility profile digest",
        )
        _validate_identity_tuple(
            self.capability_requirements,
            "capability requirements",
        )
        _validate_identity_tuple(self.platform_support, "platform support")
        if self.command is not None and (
            not self.command
            or any(not token or "\x00" in token for token in self.command)
        ):
            raise ValueError("structured route command is invalid")
        if not isinstance(self.source_reviewed, bool):
            raise ValueError("source reviewed flag is invalid")


@dataclass(frozen=True)
class RouteCapabilitySnapshotV1:
    """Generic immutable capability evidence for one structured route."""

    route_id: str
    compatibility_profile_digest: str
    snapshot_digest: str
    state: CapabilitySnapshotState
    capabilities: tuple[CapabilityCatalogFactV1, ...]

    def __post_init__(self) -> None:
        _validate_identity(self.route_id, "route id")
        _validate_digest(
            self.compatibility_profile_digest,
            "compatibility profile digest",
        )
        _validate_digest(self.snapshot_digest, "capability snapshot digest")
        if not isinstance(self.state, CapabilitySnapshotState):
            raise ValueError("capability snapshot state is invalid")
        _validate_capability_facts(self.capabilities)


@dataclass(frozen=True)
class CapabilityCatalogRouteV1:
    """One route descriptor plus current, stale, or unknown evidence."""

    descriptor: StructuredRouteDescriptorV1
    snapshot_state: CapabilitySnapshotState
    capability_snapshot_digest: str
    capabilities: tuple[CapabilityCatalogFactV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, StructuredRouteDescriptorV1):
            raise ValueError("capability catalog route descriptor is invalid")
        if not isinstance(self.snapshot_state, CapabilitySnapshotState):
            raise ValueError("capability catalog snapshot state is invalid")
        _validate_digest(
            self.capability_snapshot_digest,
            "capability snapshot digest",
        )
        if (
            self.snapshot_state is CapabilitySnapshotState.UNKNOWN
            and self.capability_snapshot_digest != UNKNOWN_CAPABILITY_SNAPSHOT_DIGEST
        ):
            raise ValueError("unknown capability evidence must use the sentinel digest")
        _validate_capability_facts(self.capabilities)
        capability_ids = {item.capability_id for item in self.capabilities}
        if not set(self.descriptor.capability_requirements).issubset(capability_ids):
            raise ValueError("catalog route omits a declared capability requirement")


@dataclass(frozen=True)
class CapabilityCatalogV1:
    """Bounded deterministic read model consumed by Route Advisor."""

    routes: tuple[CapabilityCatalogRouteV1, ...]
    digest: str
    schema_version: int = CAPABILITY_CATALOG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CAPABILITY_CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported capability catalog schema_version")
        route_ids = tuple(item.descriptor.route_id for item in self.routes)
        if route_ids != tuple(sorted(set(route_ids))):
            raise ValueError("capability catalog routes must be sorted and unique")
        _validate_digest(self.digest, "capability catalog digest")


def bind_structured_route_descriptors(
    profiles: tuple[AgentProfileV1, ...],
) -> tuple[StructuredRouteDescriptorV1, ...]:
    """Bind every profile route to exact profile and compatibility evidence."""
    if not isinstance(profiles, tuple) or not all(
        isinstance(profile, AgentProfileV1) for profile in profiles
    ):
        raise ValueError("agent profiles must be a tuple of AgentProfileV1 values")
    descriptors: list[StructuredRouteDescriptorV1] = []
    for profile in profiles:
        compatibility = {
            item.compatibility_profile_id: item
            for item in profile.compatibility_profiles
        }
        for route in profile.structured_routes:
            evidence = compatibility[route.compatibility_profile_id]
            command = (
                None
                if route.command_ref is None
                else (
                    route.command_ref.executable_name,
                    *route.command_ref.arguments,
                )
            )
            descriptors.append(
                StructuredRouteDescriptorV1(
                    agent_id=profile.agent_id,
                    profile_version=profile.profile_version,
                    profile_digest=profile.profile_digest,
                    route_id=route.route_id,
                    harness_id=route.harness_id,
                    transport_kind=route.transport_kind,
                    compatibility_profile_id=route.compatibility_profile_id,
                    compatibility_profile_digest=evidence.evidence_digest,
                    command=command,
                    capability_requirements=route.capability_requirements,
                    platform_support=profile.platform_support,
                    version_policy_kind=route.version_policy.kind.value,
                    exact_version=route.version_policy.exact_version,
                    minimum_version=route.version_policy.minimum,
                    maximum_exclusive_version=(route.version_policy.maximum_exclusive),
                    source_trust_class=profile.source.trust_class.value,
                    source_reviewed=profile.source.reviewed,
                )
            )
    route_ids = tuple(item.route_id for item in descriptors)
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("structured route ids must be globally unique")
    return tuple(sorted(descriptors, key=lambda item: item.route_id))


def project_acp_capability_snapshot(
    route_id: str,
    snapshot: AcpCapabilitySnapshotV1,
    *,
    current: bool = True,
) -> RouteCapabilitySnapshotV1:
    """Project an ACP snapshot without treating profile metadata as capability."""
    if not isinstance(snapshot, AcpCapabilitySnapshotV1):
        raise ValueError("ACP capability snapshot is invalid")
    states: dict[str, CapabilityCatalogFactState] = {}
    for item in snapshot.negotiated_features:
        states[item.feature] = CapabilityCatalogFactState.READY
    for item in snapshot.unsupported_features:
        if item.feature in states:
            raise ValueError("ACP feature cannot be ready and unsupported")
        states[item.feature] = CapabilityCatalogFactState.UNSUPPORTED
    return RouteCapabilitySnapshotV1(
        route_id=route_id,
        compatibility_profile_digest=snapshot.compatibility_profile_digest,
        snapshot_digest=snapshot.snapshot_digest,
        state=(
            CapabilitySnapshotState.CURRENT
            if current
            else CapabilitySnapshotState.STALE
        ),
        capabilities=tuple(
            CapabilityCatalogFactV1(capability_id, state)
            for capability_id, state in sorted(states.items())
        ),
    )


def build_capability_catalog(
    descriptors: tuple[StructuredRouteDescriptorV1, ...],
    snapshots: tuple[RouteCapabilitySnapshotV1, ...] = (),
) -> CapabilityCatalogV1:
    """Build a deterministic catalog while preserving unknown capability state."""
    route_ids = tuple(item.route_id for item in descriptors)
    if route_ids != tuple(sorted(set(route_ids))):
        raise ValueError("structured route descriptors must be sorted and unique")
    snapshot_by_route = {item.route_id: item for item in snapshots}
    if len(snapshot_by_route) != len(snapshots):
        raise ValueError("capability snapshots must have unique route ids")
    unknown_routes = set(snapshot_by_route) - set(route_ids)
    if unknown_routes:
        raise ValueError("capability snapshot references an unknown route")

    routes: list[CapabilityCatalogRouteV1] = []
    for descriptor in descriptors:
        snapshot = snapshot_by_route.get(descriptor.route_id)
        if snapshot is None:
            facts = tuple(
                CapabilityCatalogFactV1(
                    capability_id,
                    CapabilityCatalogFactState.UNKNOWN,
                )
                for capability_id in descriptor.capability_requirements
            )
            snapshot_state = CapabilitySnapshotState.UNKNOWN
            snapshot_digest = UNKNOWN_CAPABILITY_SNAPSHOT_DIGEST
        else:
            if (
                snapshot.compatibility_profile_digest
                != descriptor.compatibility_profile_digest
            ):
                raise ValueError("capability snapshot compatibility profile drifted")
            by_capability = {
                item.capability_id: item.state for item in snapshot.capabilities
            }
            for capability_id in descriptor.capability_requirements:
                by_capability.setdefault(
                    capability_id,
                    CapabilityCatalogFactState.UNKNOWN,
                )
            facts = tuple(
                CapabilityCatalogFactV1(capability_id, state)
                for capability_id, state in sorted(by_capability.items())
            )
            snapshot_state = snapshot.state
            snapshot_digest = snapshot.snapshot_digest
        routes.append(
            CapabilityCatalogRouteV1(
                descriptor=descriptor,
                snapshot_state=snapshot_state,
                capability_snapshot_digest=snapshot_digest,
                capabilities=facts,
            )
        )
    payload = {
        "schema_version": CAPABILITY_CATALOG_SCHEMA_VERSION,
        "routes": [_route_payload(route) for route in routes],
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CapabilityCatalogV1(routes=tuple(routes), digest=digest)


def _route_payload(route: CapabilityCatalogRouteV1) -> dict[str, object]:
    descriptor = route.descriptor
    return {
        "agent_id": descriptor.agent_id,
        "profile_version": descriptor.profile_version,
        "profile_digest": descriptor.profile_digest,
        "route_id": descriptor.route_id,
        "harness_id": descriptor.harness_id,
        "transport_kind": descriptor.transport_kind,
        "compatibility_profile_id": descriptor.compatibility_profile_id,
        "compatibility_profile_digest": descriptor.compatibility_profile_digest,
        "command": list(descriptor.command) if descriptor.command is not None else None,
        "capability_requirements": list(descriptor.capability_requirements),
        "platform_support": list(descriptor.platform_support),
        "version_policy": {
            "kind": descriptor.version_policy_kind,
            "exact": descriptor.exact_version,
            "minimum": descriptor.minimum_version,
            "maximum_exclusive": descriptor.maximum_exclusive_version,
        },
        "source": {
            "trust_class": descriptor.source_trust_class,
            "reviewed": descriptor.source_reviewed,
        },
        "snapshot_state": route.snapshot_state.value,
        "capability_snapshot_digest": route.capability_snapshot_digest,
        "capabilities": [
            {"id": item.capability_id, "state": item.state.value}
            for item in route.capabilities
        ],
    }


def _validate_capability_facts(
    capabilities: tuple[CapabilityCatalogFactV1, ...],
) -> None:
    if not all(isinstance(item, CapabilityCatalogFactV1) for item in capabilities):
        raise ValueError("capability facts are invalid")
    identities = tuple(item.capability_id for item in capabilities)
    if identities != tuple(sorted(set(identities))):
        raise ValueError("capability facts must be sorted and unique")


def _validate_identity(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_identity_tuple(values: tuple[str, ...], field_name: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field_name} must be sorted and unique")
    for value in values:
        _validate_identity(value, field_name)


def _validate_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 digest")


__all__ = [
    "CAPABILITY_CATALOG_SCHEMA_VERSION",
    "UNKNOWN_CAPABILITY_SNAPSHOT_DIGEST",
    "CapabilityCatalogFactState",
    "CapabilityCatalogFactV1",
    "CapabilityCatalogRouteV1",
    "CapabilityCatalogV1",
    "CapabilitySnapshotState",
    "RouteCapabilitySnapshotV1",
    "StructuredRouteDescriptorV1",
    "bind_structured_route_descriptors",
    "build_capability_catalog",
    "project_acp_capability_snapshot",
]
