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
from gigaloom.harnesses.agent_profiles.registry import AgentProfileRegistry
from gigaloom.harnesses.agent_profiles.resolution import (
    RELEASE_RESERVED_CORE_COMMANDS,
    build_core_command_collision_contract,
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
