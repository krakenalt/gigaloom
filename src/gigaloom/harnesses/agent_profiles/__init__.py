"""Declarative agent profile contracts."""

from gigaloom.harnesses.agent_profiles.api import (
    AgentProfileSource,
    AgentProfileSourceKind,
    AgentProfileTrustClass,
    AgentProfileV1,
    AuthOwner,
    CompatibilityProfileRef,
    CoreCommandCollisionContractV1,
    ExecutableCommandRef,
    StructuredAgentRouteRef,
    VersionPolicy,
    VersionPolicyKind,
    load_builtin_agent_profiles,
)

__all__ = [
    "AgentProfileSource",
    "AgentProfileSourceKind",
    "AgentProfileTrustClass",
    "AgentProfileV1",
    "AuthOwner",
    "CompatibilityProfileRef",
    "CoreCommandCollisionContractV1",
    "ExecutableCommandRef",
    "StructuredAgentRouteRef",
    "VersionPolicy",
    "VersionPolicyKind",
    "load_builtin_agent_profiles",
]
