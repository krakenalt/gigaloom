"""Deterministic local Agent Profile generation from exact install evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, cast

from gigaloom.contracts import ACPRegistryEntryV1, ManagedAgentArtifactV1
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.models import (
    AGENT_PROFILE_SCHEMA_VERSION,
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
from gigaloom.harnesses.agent_profiles.manifests import (
    decode_agent_profile_manifest,
)


MANAGED_ACP_REQUIRED_CAPABILITIES = (
    "cancellation",
    "session_new",
    "structured_prompt",
)


def generate_managed_agent_profile(
    entry: ACPRegistryEntryV1,
    artifact: ManagedAgentArtifactV1,
) -> AgentProfileV1:
    """Generate one structured-only negotiated route with exact provenance."""
    if entry.registry_id != artifact.registry_id or entry.version != artifact.version:
        raise ValueError("managed artifact is not bound to the ACP registry entry")
    compatibility_id = f"{artifact.local_agent_id}.acp.v1"
    route_id = f"{artifact.local_agent_id}.acp"
    executable_name = Path(artifact.executable_relative_path).name
    compatibility_evidence = canonical_digest(
        {
            "entry_digest": entry.entry_digest,
            "snapshot_digest": entry.snapshot_digest,
            "install_id": artifact.install_id,
            "artifact_digest": artifact.artifact_digest,
            "lock_digest": artifact.lock_digest,
        }
    )
    profile_payload = {
        "schema_version": AGENT_PROFILE_SCHEMA_VERSION,
        "agent_id": artifact.local_agent_id,
        "display_name": entry.name,
        "aliases": [],
        "profile_version": entry.version,
        "source": {
            "kind": AgentProfileSourceKind.ACP_REGISTRY_SNAPSHOT.value,
            "origin": f"acp-registry:{entry.registry_id}",
            "revision": entry.version,
            "trust_class": AgentProfileTrustClass.DISCOVERED.value,
            "reviewed": False,
        },
        "native": None,
        "structured_routes": [
            {
                "route_id": route_id,
                "harness_id": "acp-gateway",
                "transport_kind": "acp_stdio_v1",
                "compatibility_profile_id": compatibility_id,
                "executable_name": executable_name,
                "arguments": list(artifact.arguments),
                "capability_requirements": list(MANAGED_ACP_REQUIRED_CAPABILITIES),
                "version_policy": VersionPolicyKind.NEGOTIATED.value,
            }
        ],
        "auth_owner": AuthOwner.PROVIDER.value,
        "platform_support": [_profile_platform(artifact.platform)],
        "compatibility_profiles": [
            {
                "compatibility_profile_id": compatibility_id,
                "revision": entry.version,
                "evidence_digest": compatibility_evidence,
            }
        ],
        "artifact_digest": artifact.artifact_digest,
        "install_id": artifact.install_id,
    }
    profile_digest = canonical_digest(profile_payload)
    source = AgentProfileSource(
        kind=AgentProfileSourceKind.ACP_REGISTRY_SNAPSHOT,
        origin=f"acp-registry:{entry.registry_id}",
        revision=entry.version,
        digest=profile_digest,
        trust_class=AgentProfileTrustClass.DISCOVERED,
        reviewed=False,
    )
    compatibility = CompatibilityProfileRef(
        compatibility_profile_id=compatibility_id,
        revision=entry.version,
        evidence_digest=compatibility_evidence,
    )
    route = StructuredAgentRouteRef(
        route_id=route_id,
        harness_id="acp-gateway",
        transport_kind="acp_stdio_v1",
        compatibility_profile_id=compatibility_id,
        command_ref=ExecutableCommandRef(
            executable_name=executable_name,
            arguments=artifact.arguments,
        ),
        capability_requirements=MANAGED_ACP_REQUIRED_CAPABILITIES,
        version_policy=VersionPolicy(kind=VersionPolicyKind.NEGOTIATED),
    )
    return AgentProfileV1(
        schema_version=AGENT_PROFILE_SCHEMA_VERSION,
        agent_id=artifact.local_agent_id,
        display_name=entry.name,
        aliases=(),
        profile_version=entry.version,
        source=source,
        native=None,
        structured_routes=(route,),
        auth_owner=AuthOwner.PROVIDER,
        platform_support=(_profile_platform(artifact.platform),),
        compatibility_profiles=(compatibility,),
        profile_digest=profile_digest,
    )


def generated_profile_to_dict(profile: AgentProfileV1) -> dict[str, object]:
    """Serialize the generated structured-only profile for private state."""
    route = profile.structured_routes[0]
    compatibility = profile.compatibility_profiles[0]
    assert route.command_ref is not None
    return {
        "schema_version": profile.schema_version,
        "agent_id": profile.agent_id,
        "display_name": profile.display_name,
        "aliases": list(profile.aliases),
        "profile_version": profile.profile_version,
        "profile_digest": profile.profile_digest,
        "auth_owner": profile.auth_owner.value,
        "platform_support": list(profile.platform_support),
        "source": {
            "kind": profile.source.kind.value,
            "origin": profile.source.origin,
            "revision": profile.source.revision,
            "digest": profile.source.digest,
            "trust_class": profile.source.trust_class.value,
            "reviewed": profile.source.reviewed,
        },
        "native": None,
        "compatibility_profiles": [
            {
                "compatibility_profile_id": compatibility.compatibility_profile_id,
                "revision": compatibility.revision,
                "evidence_digest": compatibility.evidence_digest,
            }
        ],
        "structured_routes": [
            {
                "route_id": route.route_id,
                "harness_id": route.harness_id,
                "transport_kind": route.transport_kind,
                "compatibility_profile_id": route.compatibility_profile_id,
                "command_ref": {
                    "executable_name": route.command_ref.executable_name,
                    "arguments": list(route.command_ref.arguments),
                },
                "capability_requirements": list(route.capability_requirements),
                "version_policy": {"kind": route.version_policy.kind.value},
            }
        ],
    }


def generated_profile_from_dict(value: Mapping[str, object]) -> AgentProfileV1:
    """Strictly restore one generated profile from private state."""
    expected = {
        "schema_version",
        "agent_id",
        "display_name",
        "aliases",
        "profile_version",
        "profile_digest",
        "auth_owner",
        "platform_support",
        "source",
        "native",
        "compatibility_profiles",
        "structured_routes",
    }
    if set(value) != expected or not isinstance(value.get("profile_digest"), str):
        raise ValueError("generated profile state fields are invalid")
    document = dict(value)
    profile_digest = cast(str, document.pop("profile_digest"))
    source = document.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("generated profile source state is invalid")
    source_document = dict(source)
    if source_document.pop("digest", None) != profile_digest:
        raise ValueError("generated profile source digest is invalid")
    document["source"] = source_document
    profile = decode_agent_profile_manifest(document, manifest_digest=profile_digest)
    if profile.native is not None or len(profile.structured_routes) != 1:
        raise ValueError("generated managed profile must be structured-only")
    return profile


def _profile_platform(value: str) -> str:
    return "win32" if value == "windows" else value
