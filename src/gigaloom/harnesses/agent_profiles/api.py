"""Public facade for declarative agent profile contracts."""

from gigaloom.harnesses.agent_profiles.builtins import load_builtin_agent_profiles
from gigaloom.harnesses.agent_profiles.models import (
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
