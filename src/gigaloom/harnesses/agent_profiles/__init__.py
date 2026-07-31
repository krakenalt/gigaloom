"""Declarative agent profile contracts."""

from gigaloom.harnesses.agent_profiles.api import (
    AgentProfileSource,
    AgentProfileSourceKind,
    AgentProfileTrustClass,
    AgentProfileV1,
    AgentProfileRegistry,
    AuthOwner,
    CompatibilityProfileRef,
    CoreCommandCollisionContractV1,
    ExecutableCommandRef,
    RELEASE_RESERVED_CORE_COMMANDS,
    StructuredAgentRouteRef,
    VersionPolicy,
    VersionPolicyKind,
    build_core_command_collision_contract,
    load_builtin_agent_profiles,
)

__all__ = [
    "AgentProfileSource",
    "AgentProfileSourceKind",
    "AgentProfileTrustClass",
    "AgentProfileV1",
    "AgentProfileRegistry",
    "AuthOwner",
    "CompatibilityProfileRef",
    "CoreCommandCollisionContractV1",
    "ExecutableCommandRef",
    "RELEASE_RESERVED_CORE_COMMANDS",
    "StructuredAgentRouteRef",
    "VersionPolicy",
    "VersionPolicyKind",
    "build_core_command_collision_contract",
    "load_builtin_agent_profiles",
]
