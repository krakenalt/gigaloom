"""Deterministic planning and transactional managed-agent installation."""

from gigaloom.harnesses.agent_profiles.installations.activation import (
    ManagedAgentActivationStore,
)
from gigaloom.harnesses.agent_profiles.installations.binary import (
    DEFAULT_BINARY_EXTRACTION_LIMITS,
    DEFAULT_BINARY_MAX_DOWNLOAD_BYTES,
    BinaryAgentInstaller,
    BinaryInstallResult,
    StagingRecoveryResult,
)
from gigaloom.harnesses.agent_profiles.installations.coordinator import (
    LocalAgentInstallCoordinator,
    discover_local_install_coordinator,
)
from gigaloom.harnesses.agent_profiles.installations.errors import (
    AgentInstallCancelled,
    AgentInstallError,
)
from gigaloom.harnesses.agent_profiles.installations.journal import (
    InstallCancellationToken,
)
from gigaloom.harnesses.agent_profiles.installations.models import (
    DistributionDecision,
    DistributionResolutionV1,
    InstallPlanningResult,
    InstallSelectionStatus,
)
from gigaloom.harnesses.agent_profiles.installations.locks import (
    AgentLockSet,
    build_agent_lock_set,
    read_agent_lock_file,
    write_agent_lock_file,
)
from gigaloom.harnesses.agent_profiles.installations.npx import (
    NpxAgentInstaller,
    NpxPackageResolver,
)
from gigaloom.harnesses.agent_profiles.installations.packages import (
    NpxPackageResolution,
    PackageInstallResult,
    UvxPackageResolution,
)
from gigaloom.harnesses.agent_profiles.installations.planner import (
    AgentIdentityInventory,
    AgentIdentityPlan,
    AgentInstallPlanner,
    AgentInstallPlannerPolicy,
)
from gigaloom.harnesses.agent_profiles.installations.transport import (
    BinaryDownloadRequest,
    BinaryDownloadResponse,
    BinaryDownloadTransport,
    UrllibBinaryDownloadTransport,
)
from gigaloom.harnesses.agent_profiles.installations.runtime import (
    AgentRegistrySearchPage,
    AgentRuntimeInstallCoordinator,
    AgentRuntimeRegistryPort,
    AgentRuntimeService,
    AgentRuntimeSummary,
)
from gigaloom.harnesses.agent_profiles.installations.uvx import (
    UvxAgentInstaller,
    UvxPackageResolver,
    interpreter_fingerprint,
)

__all__ = [
    "AgentIdentityInventory",
    "AgentIdentityPlan",
    "AgentInstallPlanner",
    "AgentInstallPlannerPolicy",
    "AgentLockSet",
    "AgentRegistrySearchPage",
    "AgentRuntimeInstallCoordinator",
    "AgentRuntimeRegistryPort",
    "AgentRuntimeService",
    "AgentRuntimeSummary",
    "AgentInstallCancelled",
    "AgentInstallError",
    "BinaryAgentInstaller",
    "BinaryDownloadRequest",
    "BinaryDownloadResponse",
    "BinaryDownloadTransport",
    "BinaryInstallResult",
    "DEFAULT_BINARY_EXTRACTION_LIMITS",
    "DEFAULT_BINARY_MAX_DOWNLOAD_BYTES",
    "DistributionDecision",
    "DistributionResolutionV1",
    "InstallPlanningResult",
    "InstallSelectionStatus",
    "InstallCancellationToken",
    "ManagedAgentActivationStore",
    "LocalAgentInstallCoordinator",
    "NpxAgentInstaller",
    "NpxPackageResolution",
    "NpxPackageResolver",
    "PackageInstallResult",
    "StagingRecoveryResult",
    "UrllibBinaryDownloadTransport",
    "UvxAgentInstaller",
    "UvxPackageResolution",
    "UvxPackageResolver",
    "build_agent_lock_set",
    "discover_local_install_coordinator",
    "interpreter_fingerprint",
    "read_agent_lock_file",
    "write_agent_lock_file",
]
