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
from gigaloom.harnesses.agent_profiles.sources import (
    AGENT_PROFILE_ENTRY_POINT_GROUP,
    AgentProfileDiscovery,
    AgentProfileSourceError,
    InstalledAgentProfileCandidate,
    RegistryDistributionCandidate,
    decode_registry_distribution_candidates,
    discover_installed_agent_profile_candidates,
    discover_local_agent_profiles,
    load_local_agent_profile,
)

__all__ = [
    "AgentProfileSource",
    "AgentProfileSourceKind",
    "AgentProfileTrustClass",
    "AgentProfileV1",
    "AgentProfileRegistry",
    "AgentProfileDiscovery",
    "AgentProfileSourceError",
    "AGENT_PROFILE_ENTRY_POINT_GROUP",
    "AuthOwner",
    "CompatibilityProfileRef",
    "CoreCommandCollisionContractV1",
    "ExecutableCommandRef",
    "InstalledAgentProfileCandidate",
    "RELEASE_RESERVED_CORE_COMMANDS",
    "RegistryDistributionCandidate",
    "StructuredAgentRouteRef",
    "VersionPolicy",
    "VersionPolicyKind",
    "build_core_command_collision_contract",
    "decode_registry_distribution_candidates",
    "discover_installed_agent_profile_candidates",
    "discover_local_agent_profiles",
    "load_builtin_agent_profiles",
    "load_local_agent_profile",
]
