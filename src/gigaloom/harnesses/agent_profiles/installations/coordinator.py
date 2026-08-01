"""Production coordination for one managed ACP agent install transaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil
import sys
import threading

from gigaloom.contracts import (
    ACPDistributionKind,
    ACPDistributionV1,
    ACPRegistryEntryV1,
    AgentInstallPlanV1,
    AgentLockV1,
)
from gigaloom.harnesses.agent_profiles.installations.binary import (
    BinaryAgentInstaller,
    StagingRecoveryResult,
)
from gigaloom.harnesses.agent_profiles.installations.commands import (
    PackageCommandRunner,
    SubprocessPackageCommandRunner,
)
from gigaloom.harnesses.agent_profiles.installations.journal import (
    InstallCancellationToken,
)
from gigaloom.harnesses.agent_profiles.installations.models import (
    DistributionResolutionV1,
    InstallPlanningResult,
)
from gigaloom.harnesses.agent_profiles.installations.npx import (
    NpxAgentInstaller,
    NpxPackageResolver,
)
from gigaloom.harnesses.agent_profiles.installations.packages import (
    NpxPackageResolution,
    UvxPackageResolution,
)
from gigaloom.harnesses.agent_profiles.installations.planner import (
    AgentIdentityInventory,
    AgentInstallPlanner,
    AgentInstallPlannerPolicy,
)
from gigaloom.harnesses.agent_profiles.installations.transport import (
    BinaryDownloadTransport,
    UrllibBinaryDownloadTransport,
)
from gigaloom.harnesses.agent_profiles.installations.uvx import (
    UvxAgentInstaller,
    UvxPackageResolver,
)
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAcpProbeReceipt,
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.probe import ManagedAcpProbePort
from gigaloom.harnesses.agent_profiles.onboarding.service import (
    ManagedAgentOnboardingService,
)
from gigaloom.harnesses.agent_profiles.registry.models import ACPRegistryCatalog


_MAX_PREVIEW_CACHE_ENTRIES = 32


@dataclass(frozen=True, slots=True)
class _ResolvedCandidate:
    result: InstallPlanningResult
    package_resolution: NpxPackageResolution | UvxPackageResolution | None


class LocalAgentInstallCoordinator:
    """Resolve, install, probe, and activate through one local authority owner."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        platform: str,
        architecture: str,
        probe: ManagedAcpProbePort,
        network_isolation_admitted: bool,
        npm_executable: str | Path | None = None,
        uv_executable: str | Path | None = None,
        python_executable: str | Path | None = None,
        binary_transport: BinaryDownloadTransport | None = None,
        command_runner: PackageCommandRunner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(network_isolation_admitted, bool):
            raise ValueError("network isolation admission must be boolean")
        self._data_root = Path(data_root).resolve(strict=False)
        self._platform = platform
        self._architecture = architecture
        self._probe = probe
        self._network_isolation_admitted = network_isolation_admitted
        self._npm = Path(npm_executable) if npm_executable is not None else None
        self._uv = Path(uv_executable) if uv_executable is not None else None
        self._python = (
            Path(python_executable) if python_executable is not None else None
        )
        self._binary_transport = binary_transport or UrllibBinaryDownloadTransport()
        self._runner = command_runner or SubprocessPackageCommandRunner()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._preview_cache: dict[str, _ResolvedCandidate] = {}
        self._preview_lock = threading.Lock()
        AgentInstallPlannerPolicy(
            platform=platform,
            architecture=architecture,
            data_root=str(self._data_root),
        )

    def preview(
        self,
        entry: ACPRegistryEntryV1,
        catalog: ACPRegistryCatalog,
        *,
        inventory: AgentIdentityInventory,
        local_agent_id: str | None,
    ) -> InstallPlanningResult:
        """Resolve only the package evidence needed by the preference policy."""
        planner = self._planner()
        initial = planner.plan(
            entry,
            catalog.snapshot,
            inventory=inventory,
            local_agent_id=local_agent_id,
            now=self._now(),
        )
        if initial.reason_code == "identity_collision_requires_explicit_alias":
            return initial
        if initial.plan is not None and _verified_binary(initial.plan):
            self._remember(_ResolvedCandidate(initial, None))
            return initial

        resolutions: list[DistributionResolutionV1] = []
        package_resolutions: dict[str, NpxPackageResolution | UvxPackageResolution] = {}
        self._resolve_npx(entry, resolutions, package_resolutions)
        result = self._plan_with(
            planner,
            entry,
            catalog,
            inventory=inventory,
            local_agent_id=local_agent_id,
            resolutions=resolutions,
        )
        if result.plan is not None and (
            result.plan.distribution_kind is ACPDistributionKind.NPX
        ):
            self._remember(
                _ResolvedCandidate(
                    result,
                    package_resolutions[_selected_digest(entry, result.plan)],
                )
            )
            return result

        self._resolve_uvx(entry, resolutions, package_resolutions)
        result = self._plan_with(
            planner,
            entry,
            catalog,
            inventory=inventory,
            local_agent_id=local_agent_id,
            resolutions=resolutions,
        )
        package_resolution = None
        if result.plan is not None:
            package_resolution = package_resolutions.get(
                _selected_digest(entry, result.plan)
            )
            self._remember(_ResolvedCandidate(result, package_resolution))
        return result

    def install(
        self,
        entry: ACPRegistryEntryV1,
        catalog: ACPRegistryCatalog,
        *,
        inventory: AgentIdentityInventory,
        local_agent_id: str | None,
        confirmed: bool,
        allow_unverified: bool,
        expected_plan_id: str | None = None,
        cancellation: InstallCancellationToken | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> ManagedAgentOnboardingResult:
        """Execute exactly one confirmed install and onboarding transaction."""
        if not confirmed:
            raise ValueError("managed agent install requires explicit confirmation")
        self._require_network_isolation()
        _checkpoint(cancellation, progress, "resolving", "registry_entry_selected")
        if expected_plan_id is None:
            result = self.preview(
                entry,
                catalog,
                inventory=inventory,
                local_agent_id=local_agent_id,
            )
            candidate = self._candidate(result)
        else:
            candidate = self._reviewed_candidate(
                expected_plan_id,
                entry,
                catalog,
                inventory=inventory,
                local_agent_id=local_agent_id,
            )
            result = candidate.result
        _checkpoint(cancellation, progress, "planned", result.reason_code)
        return self._execute(
            entry,
            candidate,
            allow_unverified=allow_unverified,
            cancellation=cancellation,
            progress=progress,
        )

    def probe(
        self,
        record: ManagedAgentOnboardingResult,
    ) -> ManagedAcpProbeReceipt:
        """Run another initialize-only probe under the admitted isolation owner."""
        self._require_network_isolation()
        return self._probe.probe(
            record.profile,
            record.artifact,
            network_isolated=True,
        )

    def sync(
        self,
        lock: AgentLockV1,
        entry: ACPRegistryEntryV1,
        catalog: ACPRegistryCatalog,
        *,
        inventory: AgentIdentityInventory,
        confirmed: bool,
    ) -> ManagedAgentOnboardingResult:
        """Install the exact lock candidate and reject every substitution."""
        if not confirmed:
            raise ValueError("managed agent sync requires explicit confirmation")
        self._require_network_isolation()
        result = self.preview(
            entry,
            catalog,
            inventory=inventory,
            local_agent_id=lock.local_agent_id,
        )
        candidate = self._candidate(result)
        if not _candidate_matches_lock(candidate, lock):
            raise ValueError("resolved managed agent differs from the exact lock")
        return self._execute(entry, candidate, allow_unverified=False)

    def recover_abandoned(self) -> tuple[StagingRecoveryResult, ...]:
        """Recover only staging directories carrying an owned binary journal."""
        return BinaryAgentInstaller(
            self._data_root,
            self._binary_transport,
            clock=self._clock,
        ).recover_abandoned()

    def _resolve_npx(
        self,
        entry: ACPRegistryEntryV1,
        resolutions: list[DistributionResolutionV1],
        packages: dict[str, NpxPackageResolution | UvxPackageResolution],
    ) -> None:
        candidates = tuple(
            item for item in entry.distributions if item.kind is ACPDistributionKind.NPX
        )
        if not candidates or self._npm is None:
            return
        self._require_network_isolation()
        resolver = NpxPackageResolver(self._data_root, self._npm, self._runner)
        for distribution in candidates:
            try:
                resolved = resolver.resolve(distribution)
            except (OSError, RuntimeError, ValueError):
                continue
            resolutions.append(resolved.evidence)
            packages[distribution.distribution_digest] = resolved

    def _resolve_uvx(
        self,
        entry: ACPRegistryEntryV1,
        resolutions: list[DistributionResolutionV1],
        packages: dict[str, NpxPackageResolution | UvxPackageResolution],
    ) -> None:
        candidates = tuple(
            item for item in entry.distributions if item.kind is ACPDistributionKind.UVX
        )
        if not candidates or self._uv is None or self._python is None:
            return
        self._require_network_isolation()
        resolver = UvxPackageResolver(self._data_root, self._uv, self._runner)
        for distribution in candidates:
            try:
                resolved = resolver.resolve(
                    distribution,
                    interpreter=self._python,
                )
            except (OSError, RuntimeError, ValueError):
                continue
            resolutions.append(resolved.evidence)
            packages[distribution.distribution_digest] = resolved

    def _execute(
        self,
        entry: ACPRegistryEntryV1,
        candidate: _ResolvedCandidate,
        *,
        allow_unverified: bool,
        cancellation: InstallCancellationToken | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> ManagedAgentOnboardingResult:
        plan = candidate.result.plan
        if plan is None:
            raise ValueError(_planning_failure(candidate.result))
        transitions = ()
        bytes_received = 0
        _checkpoint(cancellation, progress, "installing", "install_started")
        if plan.distribution_kind is ACPDistributionKind.BINARY:
            installed = BinaryAgentInstaller(
                self._data_root,
                self._binary_transport,
                clock=self._clock,
            ).install(
                plan,
                confirmed=True,
                allow_unverified=allow_unverified,
                cancellation=cancellation,
            )
            artifact = installed.artifact
            transitions = installed.transitions
            bytes_received = installed.bytes_received
        elif plan.distribution_kind is ACPDistributionKind.NPX:
            resolution = candidate.package_resolution
            if not isinstance(resolution, NpxPackageResolution):
                raise ValueError("exact npm package evidence is unavailable")
            artifact = (
                NpxAgentInstaller(
                    self._data_root,
                    self._required_executable(self._npm, "npm"),
                    self._runner,
                    clock=self._clock,
                )
                .install(
                    plan,
                    resolution,
                    confirmed=True,
                    allow_lifecycle_scripts=False,
                )
                .artifact
            )
        else:
            resolution = candidate.package_resolution
            if not isinstance(resolution, UvxPackageResolution):
                raise ValueError("exact uv package evidence is unavailable")
            artifact = (
                UvxAgentInstaller(
                    self._data_root,
                    self._required_executable(self._uv, "uv"),
                    self._runner,
                    clock=self._clock,
                )
                .install(plan, resolution, confirmed=True)
                .artifact
            )
        _checkpoint(cancellation, progress, "probing", "artifact_installed")
        result = ManagedAgentOnboardingService(
            str(self._data_root),
            self._probe,
            clock=self._clock,
        ).onboard(
            plan,
            entry,
            artifact,
            network_isolated=True,
            transitions=transitions,
            bytes_received=bytes_received,
        )
        if progress is not None:
            progress(
                "activated" if result.active else "retained_inactive",
                f"activation_{result.activation.status.value}",
            )
        return result

    def _plan_with(
        self,
        planner: AgentInstallPlanner,
        entry: ACPRegistryEntryV1,
        catalog: ACPRegistryCatalog,
        *,
        inventory: AgentIdentityInventory,
        local_agent_id: str | None,
        resolutions: list[DistributionResolutionV1],
    ) -> InstallPlanningResult:
        return planner.plan(
            entry,
            catalog.snapshot,
            inventory=inventory,
            local_agent_id=local_agent_id,
            resolutions=tuple(resolutions),
            now=self._now(),
        )

    def _planner(self) -> AgentInstallPlanner:
        return AgentInstallPlanner(
            AgentInstallPlannerPolicy(
                platform=self._platform,
                architecture=self._architecture,
                data_root=str(self._data_root),
            )
        )

    def _remember(self, candidate: _ResolvedCandidate) -> None:
        plan = candidate.result.plan
        if plan is None:
            return
        with self._preview_lock:
            if len(self._preview_cache) >= _MAX_PREVIEW_CACHE_ENTRIES:
                self._preview_cache.pop(next(iter(self._preview_cache)))
            self._preview_cache[plan.plan_id] = candidate

    def _candidate(self, result: InstallPlanningResult) -> _ResolvedCandidate:
        if result.plan is None:
            raise ValueError(_planning_failure(result))
        with self._preview_lock:
            candidate = self._preview_cache.pop(result.plan.plan_id, None)
        if candidate is None or candidate.result != result:
            raise ValueError("managed agent preview evidence is unavailable")
        return candidate

    def _reviewed_candidate(
        self,
        plan_id: str,
        entry: ACPRegistryEntryV1,
        catalog: ACPRegistryCatalog,
        *,
        inventory: AgentIdentityInventory,
        local_agent_id: str | None,
    ) -> _ResolvedCandidate:
        with self._preview_lock:
            candidate = self._preview_cache.pop(plan_id, None)
        plan = None if candidate is None else candidate.result.plan
        if (
            plan is None
            or plan.plan_id != plan_id
            or plan.registry_id != entry.registry_id
            or plan.entry_digest != entry.entry_digest
            or plan.snapshot_digest != catalog.snapshot.snapshot_digest
            or plan.local_agent_id != (local_agent_id or entry.registry_id)
            or plan.expires_at <= self._now()
            or inventory.collision_namespaces(plan.local_agent_id)
        ):
            raise ValueError("reviewed_agent_install_plan_changed")
        return candidate

    def _require_network_isolation(self) -> None:
        if not self._network_isolation_admitted:
            raise RuntimeError("managed agent operation requires network isolation")

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("agent install coordinator clock must be timezone-aware")
        return value

    @staticmethod
    def _required_executable(value: Path | None, label: str) -> Path:
        if value is None:
            raise ValueError(f"{label} executable is unavailable")
        return value


def discover_local_install_coordinator(
    data_root: str | Path,
    *,
    platform: str,
    architecture: str,
    probe: ManagedAcpProbePort,
    network_isolation_admitted: bool,
    clock: Callable[[], datetime] | None = None,
) -> LocalAgentInstallCoordinator:
    """Discover absolute host tools while keeping install environments private."""
    return LocalAgentInstallCoordinator(
        data_root,
        platform=platform,
        architecture=architecture,
        probe=probe,
        network_isolation_admitted=network_isolation_admitted,
        npm_executable=shutil.which("npm"),
        uv_executable=shutil.which("uv"),
        python_executable=sys.executable,
        clock=clock,
    )


def _verified_binary(plan: AgentInstallPlanV1) -> bool:
    return (
        plan.distribution_kind is ACPDistributionKind.BINARY
        and plan.expected_integrity is not None
    )


def _selected_digest(
    entry: ACPRegistryEntryV1,
    plan: AgentInstallPlanV1,
) -> str:
    matches = tuple(
        item.distribution_digest
        for item in entry.distributions
        if _distribution_matches_plan(item, plan)
    )
    if len(matches) != 1:
        raise ValueError("selected registry distribution is ambiguous")
    return matches[0]


def _distribution_matches_plan(
    distribution: ACPDistributionV1,
    plan: AgentInstallPlanV1,
) -> bool:
    return (
        distribution.kind is plan.distribution_kind
        and distribution.source == plan.source
        and distribution.package_or_archive == plan.package_or_archive
        and distribution.command == plan.command
        and distribution.arguments == plan.arguments
        and distribution.environment == plan.environment
    )


def _candidate_matches_lock(
    candidate: _ResolvedCandidate,
    lock: AgentLockV1,
) -> bool:
    plan = candidate.result.plan
    if plan is None:
        return False
    resolution = candidate.package_resolution
    artifact_digest = (
        resolution.evidence.artifact_digest
        if resolution is not None
        else plan.expected_integrity
    )
    if isinstance(artifact_digest, str):
        artifact_digest = artifact_digest.removeprefix("sha256:")
    package_integrity = (
        resolution.evidence.package_integrity
        if resolution is not None
        else plan.expected_integrity
    )
    return (
        plan.registry_id == lock.registry_id
        and plan.snapshot_digest == lock.snapshot_digest
        and plan.entry_digest == lock.entry_digest
        and plan.local_agent_id == lock.local_agent_id
        and plan.version == lock.version
        and plan.platform == lock.platform
        and plan.architecture == lock.architecture
        and plan.distribution_kind is lock.distribution_kind
        and artifact_digest == lock.artifact_digest
        and package_integrity == lock.package_integrity
        and plan.command == lock.command
        and plan.arguments == lock.arguments
        and plan.environment == lock.environment
    )


def _planning_failure(result: InstallPlanningResult) -> str:
    if result.proposed_local_agent_id is not None:
        return f"{result.reason_code}; choose --as {result.proposed_local_agent_id}"
    return result.reason_code


def _checkpoint(
    cancellation: InstallCancellationToken | None,
    progress: Callable[[str, str], None] | None,
    state: str,
    reason_code: str,
) -> None:
    if cancellation is not None and cancellation.cancelled:
        from gigaloom.harnesses.agent_profiles.installations.errors import (
            AgentInstallCancelled,
        )

        raise AgentInstallCancelled("managed_agent_install_cancelled")
    if progress is not None:
        progress(state, reason_code)
