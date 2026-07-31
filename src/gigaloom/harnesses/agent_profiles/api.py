"""Public facade for declarative agent profile contracts."""

from gigaloom.harnesses.agent_profiles.builtins import load_builtin_agent_profiles
from gigaloom.harnesses.agent_profiles.compatibility import (
    AgentProbePlan,
    AgentProbePlanStatus,
    plan_agent_probe,
)
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
from gigaloom.harnesses.agent_profiles.registrations import (
    AgentProfileRegistrationResult,
    AgentProfileRegistrationStateV1,
    AgentProfileRegistrationStore,
    AgentProfileRegistrationV1,
    AgentProfileRegistryIssue,
    AgentProfileRegistrySnapshot,
    load_agent_profile_registry,
    register_local_agent_profile,
    remove_registered_agent_profile,
)
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
    "AgentProfileRegistrationResult",
    "AgentProfileRegistrationStateV1",
    "AgentProfileRegistrationStore",
    "AgentProfileRegistrationV1",
    "AgentProfileRegistryIssue",
    "AgentProfileRegistrySnapshot",
    "AgentProfileSourceError",
    "AgentProbePlan",
    "AgentProbePlanStatus",
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
    "load_agent_profile_registry",
    "load_local_agent_profile",
    "plan_agent_probe",
    "register_local_agent_profile",
    "remove_registered_agent_profile",
]
