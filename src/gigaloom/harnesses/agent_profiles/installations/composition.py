"""Host-aware composition for the shared managed-agent runtime service."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import platform as platform_module
import sys

from gigaloom.harnesses.agent_profiles.installations.coordinator import (
    discover_local_install_coordinator,
)
from gigaloom.harnesses.agent_profiles.installations.planner import (
    AgentIdentityInventory,
)
from gigaloom.harnesses.agent_profiles.installations.runtime import (
    AgentRuntimeService,
)
from gigaloom.harnesses.agent_profiles.onboarding import (
    ManagedAcpProbeRunner,
    discover_managed_acp_network_isolation,
)
from gigaloom.harnesses.agent_profiles.registry import (
    ACPRegistryCache,
    OfficialACPRegistryClient,
)


def create_agent_runtime_service(
    data_root: str | Path,
    *,
    network_isolation_admitted: bool | None = None,
    platform_id: str | None = None,
    architecture: str | None = None,
    reserved_inventory: AgentIdentityInventory = AgentIdentityInventory(),
) -> AgentRuntimeService:
    """Create the CLI/Web lifecycle owner with explicit host authority."""

    def clock() -> datetime:
        return datetime.now(UTC)

    root = Path(data_root).expanduser().resolve(strict=False)
    host_platform = platform_id or (
        "windows" if sys.platform == "win32" else sys.platform
    )
    host_architecture = architecture or _host_architecture()
    isolation = discover_managed_acp_network_isolation(platform_id=host_platform)
    if network_isolation_admitted is True and isolation is None:
        raise RuntimeError("managed_agent_network_isolation_unavailable")
    isolation_admitted = (
        isolation is not None and network_isolation_admitted is not False
    )
    cache = ACPRegistryCache(root / "agent_profiles/acp_registry")
    return AgentRuntimeService(
        root,
        OfficialACPRegistryClient(cache=cache),
        discover_local_install_coordinator(
            root,
            platform=host_platform,
            architecture=host_architecture,
            probe=ManagedAcpProbeRunner(isolation),
            network_isolation_admitted=isolation_admitted,
            clock=clock,
        ),
        clock=clock,
        reserved_inventory=reserved_inventory,
    )


def _host_architecture() -> str:
    machine = platform_module.machine().lower()
    return {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "x64": "x86_64",
    }.get(machine, machine)


__all__ = ["create_agent_runtime_service"]
