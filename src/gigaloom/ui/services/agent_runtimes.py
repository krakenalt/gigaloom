"""Application-scoped composition for Coding Agent runtime surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.cli_commands.parser import build_parser
from gigaloom.config import HarnessConfig
from gigaloom.harnesses.agent_profiles import (
    AgentProfileSourceKind,
    AgentProfileV1,
    build_core_command_collision_contract,
    load_agent_profile_registry,
)
from gigaloom.harnesses.agent_profiles.installations import (
    AgentIdentityInventory,
    AgentRuntimeService,
    create_agent_runtime_service,
)
from gigaloom.ui.services.agent_installations import AgentInstallationWebService
from gigaloom.ui.services.agent_registry import (
    AgentRegistryWebService,
    LocalManifestWebProjection,
)


@dataclass(frozen=True, slots=True)
class AgentRuntimeWebBundle:
    """One runtime and its two presentation projections."""

    runtime: AgentRuntimeService
    registry: AgentRegistryWebService
    installations: AgentInstallationWebService
    profiles: tuple[AgentProfileV1, ...]


def build_agent_runtime_web_bundle(
    config: HarnessConfig,
    *,
    profiles: tuple[AgentProfileV1, ...] | None,
) -> AgentRuntimeWebBundle:
    """Build CLI-compatible managed state without implicit network authority."""
    commands = _registered_commands()
    base_profiles = (
        profiles
        if profiles is not None
        else load_agent_profile_registry(
            config.data_dir,
            collision_contract=build_core_command_collision_contract(commands),
        ).registry.profiles
    )
    runtime = create_agent_runtime_service(
        config.data_dir,
        reserved_inventory=_identity_inventory(commands, base_profiles),
    )
    combined = _combine_profiles(base_profiles, runtime.active_profiles())
    registry = AgentRegistryWebService(
        runtime,
        local_manifests=lambda: _local_manifest_projections(base_profiles),
    )
    return AgentRuntimeWebBundle(
        runtime=runtime,
        registry=registry,
        installations=AgentInstallationWebService(config.data_dir, runtime),
        profiles=combined,
    )


def _registered_commands() -> tuple[str, ...]:
    parser = build_parser()
    action = next(item for item in parser._actions if item.dest == "command")
    if action.choices is None:
        raise RuntimeError("root CLI parser has no command registry")
    return tuple(sorted(action.choices))


def _identity_inventory(
    commands: tuple[str, ...],
    profiles: tuple[AgentProfileV1, ...],
) -> AgentIdentityInventory:
    return AgentIdentityInventory(
        core_commands=commands,
        native_agent_ids=tuple(
            profile.agent_id for profile in profiles if profile.native is not None
        ),
        native_aliases=tuple(
            alias
            for profile in profiles
            if profile.native is not None
            for alias in profile.aliases
        ),
        local_agent_ids=tuple(
            profile.agent_id
            for profile in profiles
            if profile.source.kind is AgentProfileSourceKind.LOCAL_MANIFEST
        ),
    )


def _combine_profiles(
    base: tuple[AgentProfileV1, ...],
    managed: tuple[AgentProfileV1, ...],
) -> tuple[AgentProfileV1, ...]:
    by_id = {profile.agent_id: profile for profile in base}
    for profile in managed:
        if profile.agent_id in by_id:
            raise ValueError(
                "managed agent identity collides with a registered profile"
            )
        by_id[profile.agent_id] = profile
    return tuple(by_id[agent_id] for agent_id in sorted(by_id))


def _local_manifest_projections(
    profiles: tuple[AgentProfileV1, ...],
) -> tuple[LocalManifestWebProjection, ...]:
    return tuple(
        LocalManifestWebProjection(
            agent_id=profile.agent_id,
            display_name=profile.display_name,
            profile_digest=profile.profile_digest,
            source=profile.source.kind.value,
            structured_route_ids=tuple(
                route.route_id for route in profile.structured_routes
            ),
            native_available=profile.native is not None,
        )
        for profile in profiles
        if profile.source.kind is AgentProfileSourceKind.LOCAL_MANIFEST
    )


__all__ = ["AgentRuntimeWebBundle", "build_agent_runtime_web_bundle"]
