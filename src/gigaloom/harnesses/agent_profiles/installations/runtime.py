"""Shared CLI/Web lifecycle service for managed ACP Registry agents."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import shutil
from typing import Callable, Protocol, runtime_checkable

from gigaloom.contracts import (
    ACPRegistryEntryV1,
    AgentActivationV1,
    AgentLockV1,
)
from gigaloom.harnesses.agent_profiles.installations.activation import (
    ManagedAgentActivationStore,
)
from gigaloom.harnesses.agent_profiles.installations.filesystem import require_within
from gigaloom.harnesses.agent_profiles.installations.locks import (
    AgentLockSet,
    build_agent_lock_set,
    read_agent_lock_file,
    write_agent_lock_file,
)
from gigaloom.harnesses.agent_profiles.installations.journal import (
    InstallCancellationToken,
)
from gigaloom.harnesses.agent_profiles.installations.binary import (
    StagingRecoveryResult,
)
from gigaloom.harnesses.agent_profiles.installations.planner import (
    AgentIdentityInventory,
)
from gigaloom.harnesses.agent_profiles.installations.readiness import (
    AgentRuntimeReadiness,
    project_agent_runtime_readiness,
)
from gigaloom.harnesses.agent_profiles.installations.models import (
    InstallPlanningResult,
)
from gigaloom.harnesses.agent_profiles.models import AgentProfileV1
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAcpProbeReceipt,
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.reactivation import (
    ManagedAgentReactivationService,
)
from gigaloom.harnesses.agent_profiles.onboarding.store import (
    ManagedOnboardingStore,
)
from gigaloom.harnesses.agent_profiles.registry.index import ACPRegistryIndex
from gigaloom.harnesses.agent_profiles.registry.models import ACPRegistryCatalog


@runtime_checkable
class AgentRuntimeRegistryPort(Protocol):
    """Validated registry revision owner used by the lifecycle service."""

    def catalog(self, *, refresh: bool = False) -> ACPRegistryCatalog:
        """Return one explicit cached/refreshed revision."""


@runtime_checkable
class AgentRuntimeInstallCoordinator(Protocol):
    """One backend-owned install/probe transaction boundary."""

    def preview(
        self,
        entry: ACPRegistryEntryV1,
        catalog: ACPRegistryCatalog,
        *,
        inventory: AgentIdentityInventory,
        local_agent_id: str | None,
    ) -> InstallPlanningResult:
        """Resolve and plan without installing."""

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
        """Execute one confirmed add/update transaction."""

    def probe(
        self,
        record: ManagedAgentOnboardingResult,
    ) -> ManagedAcpProbeReceipt:
        """Re-probe one installed artifact without activating it."""

    def sync(
        self,
        lock: AgentLockV1,
        entry: ACPRegistryEntryV1,
        catalog: ACPRegistryCatalog,
        *,
        inventory: AgentIdentityInventory,
        confirmed: bool,
    ) -> ManagedAgentOnboardingResult:
        """Install exactly one lock entry without upgrading it."""

    def recover_abandoned(self) -> tuple[StagingRecoveryResult, ...]:
        """Recover only owned abandoned install staging directories."""

    def require_install_authority(self) -> None:
        """Reject mutation when the composition has no isolation owner."""


@dataclass(frozen=True, slots=True)
class AgentRegistrySearchPage:
    """One local registry search bound to an exact cache revision."""

    catalog: ACPRegistryCatalog
    entries: tuple[ACPRegistryEntryV1, ...]


@dataclass(frozen=True, slots=True)
class AgentRuntimeSummary:
    """Stable installed-agent projection shared by CLI and Web."""

    local_agent_id: str
    registry_id: str
    version: str
    install_id: str
    distribution_kind: str
    active: bool
    activation_status: str
    probe_state: str
    auth_required: bool
    readiness: AgentRuntimeReadiness


class AgentRuntimeService:
    """Own search/add/update/remove/lock decisions for every presentation layer."""

    def __init__(
        self,
        data_root: str | Path,
        registry: AgentRuntimeRegistryPort,
        coordinator: AgentRuntimeInstallCoordinator,
        *,
        clock,
        reserved_inventory: AgentIdentityInventory = AgentIdentityInventory(),
    ) -> None:  # noqa: ANN001
        self._data_root = Path(data_root).expanduser().resolve(strict=False)
        self._registry = registry
        self._coordinator = coordinator
        self._clock = clock
        self._records = ManagedOnboardingStore(self._data_root)
        self._activations = ManagedAgentActivationStore(self._data_root)
        self._reactivation = ManagedAgentReactivationService(
            self._data_root,
            clock=clock,
        )
        self._reserved = reserved_inventory

    def refresh(self) -> ACPRegistryCatalog:
        """Explicitly refresh the validated official registry cache."""
        return self._registry.catalog(refresh=True)

    def require_install_authority(self) -> None:
        """Validate install authority before creating an async operation."""
        self._coordinator.require_install_authority()

    def search(
        self,
        query: str,
        *,
        refresh: bool = False,
        limit: int = 20,
    ) -> AgentRegistrySearchPage:
        """Search one immutable local index; refresh only when explicitly requested."""
        catalog = self._registry.catalog(refresh=refresh)
        return AgentRegistrySearchPage(
            catalog=catalog,
            entries=ACPRegistryIndex.build(catalog.entries).search(query, limit=limit),
        )

    def add(
        self,
        query: str,
        *,
        local_agent_id: str | None = None,
        dry_run: bool = False,
        confirmed: bool = False,
        allow_unverified: bool = False,
        refresh: bool = False,
        expected_plan_id: str | None = None,
        cancellation: InstallCancellationToken | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> InstallPlanningResult | ManagedAgentOnboardingResult:
        """Resolve one unique entry, then preview or execute one transaction."""
        catalog = self._registry.catalog(refresh=refresh)
        entry = _resolve_unique_entry(catalog, query)
        inventory = self._inventory()
        if dry_run:
            return self._coordinator.preview(
                entry,
                catalog,
                inventory=inventory,
                local_agent_id=local_agent_id,
            )
        if not confirmed:
            raise ValueError("managed agent add requires explicit confirmation")
        return self._coordinator.install(
            entry,
            catalog,
            inventory=inventory,
            local_agent_id=local_agent_id,
            confirmed=True,
            allow_unverified=allow_unverified,
            expected_plan_id=expected_plan_id,
            cancellation=cancellation,
            progress=progress,
        )

    def list(self) -> tuple[AgentRuntimeSummary, ...]:
        """Return every immutable installed revision with current-pointer state."""
        records = tuple(
            sorted(
                self._records.records(),
                key=lambda item: (
                    item.artifact.local_agent_id,
                    item.artifact.version,
                    item.artifact.install_id,
                ),
            )
        )
        current_by_agent = {
            local_agent_id: self._activations.current(local_agent_id)
            for local_agent_id in {item.artifact.local_agent_id for item in records}
        }
        active_activation_ids = {
            current[0].install_id: current[1].activation_id
            for current in current_by_agent.values()
            if current is not None
        }
        projected = self._reactivation.project_many(
            records,
            active_activation_ids=active_activation_ids,
        )
        return tuple(
            _summary(
                record,
                current_by_agent[record.artifact.local_agent_id],
            )
            for record in projected
        )

    def inspect(self, local_agent_id: str) -> ManagedAgentOnboardingResult:
        """Return the current record or latest inactive revision for one local id."""
        records = self._records_for(local_agent_id)
        current = self._activations.current(local_agent_id)
        if current is not None:
            record = next(
                item
                for item in records
                if item.artifact.install_id == current[0].install_id
            )
            return self._project_record(record)
        return self._project_record(
            max(records, key=lambda item: item.artifact.installed_at)
        )

    def active_profiles(self) -> tuple[AgentProfileV1, ...]:
        """Return generated profiles for active managed revisions only."""
        active_ids = {item.local_agent_id for item in self.list() if item.active}
        return tuple(self.inspect(agent_id).profile for agent_id in sorted(active_ids))

    def probe(self, local_agent_id: str) -> ManagedAcpProbeReceipt:
        """Run an initialize-only probe through the same backend coordinator."""
        return self._coordinator.probe(self.inspect(local_agent_id))

    def activate(
        self,
        local_agent_id: str,
        install_id: str | None = None,
        *,
        confirmed: bool,
    ) -> ManagedAgentOnboardingResult:
        """Re-probe and atomically activate one exact retained ACP revision."""
        if not confirmed:
            raise ValueError("managed agent activation requires explicit confirmation")
        record = self._activation_candidate(local_agent_id, install_id=install_id)
        probe = self._coordinator.probe(record)
        return self._reactivation.activate(record, probe)

    def outdated(self, *, refresh: bool = False) -> tuple[AgentRuntimeSummary, ...]:
        """Report installed revisions differing from the current exact entry only."""
        catalog = self._registry.catalog(refresh=refresh)
        by_id = {entry.registry_id: entry for entry in catalog.entries}
        return tuple(
            item
            for item in self.list()
            if item.registry_id in by_id
            and item.version != by_id[item.registry_id].version
        )

    def update(
        self,
        local_agent_id: str,
        *,
        confirmed: bool,
        allow_unverified: bool = False,
        refresh: bool = False,
        cancellation: InstallCancellationToken | None = None,
        progress: Callable[[str, str], None] | None = None,
    ) -> ManagedAgentOnboardingResult:
        """Install the explicit current registry entry side-by-side, never silently."""
        if not confirmed:
            raise ValueError("managed agent update requires explicit confirmation")
        current = self.inspect(local_agent_id)
        catalog = self._registry.catalog(refresh=refresh)
        entry = next(
            (
                item
                for item in catalog.entries
                if item.registry_id == current.artifact.registry_id
            ),
            None,
        )
        if entry is None:
            raise ValueError(
                "installed registry agent is absent from the current revision"
            )
        if entry.version == current.artifact.version:
            raise ValueError("managed agent is already at the current registry version")
        return self._coordinator.install(
            entry,
            catalog,
            inventory=self._inventory(exclude_local_agent_id=local_agent_id),
            local_agent_id=local_agent_id,
            confirmed=True,
            allow_unverified=allow_unverified,
            cancellation=cancellation,
            progress=progress,
        )

    def rollback(self, local_agent_id: str) -> AgentActivationV1:
        """Atomically restore the retained previous install for one local id."""
        current = self._activations.current(local_agent_id)
        if current is None or current[1].previous_install_id is None:
            raise ValueError("managed agent rollback is unavailable")
        previous = next(
            item
            for item in self._records_for(local_agent_id)
            if item.artifact.install_id == current[1].previous_install_id
        )
        return self._activations.rollback(
            local_agent_id,
            profile_digest=previous.profile.profile_digest,
            compatibility_observation_digest=previous.compatibility.probe_digest,
            activated_at=self._now(),
        )

    def remove(self, local_agent_id: str, *, confirmed: bool) -> int:
        """Remove only managed artifacts/state for one explicit local identity."""
        if not confirmed:
            raise ValueError("managed agent removal requires explicit confirmation")
        records = self._records_for(local_agent_id)
        self._activations.deactivate(local_agent_id)
        managed_root = self._data_root / "agents" / "registry"
        removed = 0
        for record in records:
            path = require_within(
                Path(record.artifact.managed_root),
                managed_root,
                reason_code="managed_remove_outside_authority",
            )
            if path.is_symlink():
                raise ValueError("managed artifact root cannot be a symlink")
            shutil.rmtree(path)
            self._records.remove_record(record.artifact.install_id)
            removed += 1
        return removed

    def recover_abandoned(self) -> tuple[StagingRecoveryResult, ...]:
        """Recover only coordinator-owned abandoned install staging state."""
        return self._coordinator.recover_abandoned()

    def lock(self, output: str | Path) -> AgentLockSet:
        """Write byte-stable locks for current active pointers only."""
        active_records = []
        for local_agent_id in sorted(
            {item.artifact.local_agent_id for item in self._records.records()}
        ):
            current = self._activations.current(local_agent_id)
            if current is None:
                continue
            active_records.append(self.inspect(local_agent_id))
        lockset = build_agent_lock_set(tuple(active_records))
        write_agent_lock_file(output, lockset)
        return lockset

    def sync(
        self,
        lock_path: str | Path,
        *,
        confirmed: bool,
    ) -> tuple[ManagedAgentOnboardingResult, ...]:
        """Install only exact lock entries; never substitute a newer revision."""
        if not confirmed:
            raise ValueError("managed agent sync requires explicit confirmation")
        lockset = read_agent_lock_file(lock_path)
        catalog = self._registry.catalog(refresh=False)
        existing = self._records.records()
        results = []
        for lock in lockset.agents:
            if any(_record_matches_lock(item, lock) for item in existing):
                continue
            if catalog.snapshot.snapshot_digest != lock.snapshot_digest:
                raise ValueError("agent sync exact registry snapshot is unavailable")
            entry = next(
                (
                    item
                    for item in catalog.entries
                    if item.registry_id == lock.registry_id
                    and item.entry_digest == lock.entry_digest
                    and item.version == lock.version
                ),
                None,
            )
            if entry is None:
                raise ValueError("agent sync exact registry entry is unavailable")
            result = self._coordinator.sync(
                lock,
                entry,
                catalog,
                inventory=self._inventory(exclude_local_agent_id=lock.local_agent_id),
                confirmed=True,
            )
            if not _record_matches_lock(result, lock):
                raise ValueError("agent sync result differs from the exact lock")
            results.append(result)
        return tuple(results)

    def _records_for(
        self,
        local_agent_id: str,
    ) -> tuple[ManagedAgentOnboardingResult, ...]:
        records = tuple(
            item
            for item in self._records.records()
            if item.artifact.local_agent_id == local_agent_id
        )
        if not records:
            raise ValueError(f"unknown managed agent: {local_agent_id}")
        return records

    def _activation_candidate(
        self,
        local_agent_id: str,
        *,
        install_id: str | None,
    ) -> ManagedAgentOnboardingResult:
        records = self._records_for(local_agent_id)
        current = self._activations.current(local_agent_id)
        current_id = current[0].install_id if current is not None else None
        candidates = tuple(
            item
            for item in records
            if item.artifact.install_id != current_id
            and (install_id is None or item.artifact.install_id == install_id)
        )
        if not candidates:
            if install_id == current_id or (
                install_id is None and current_id is not None
            ):
                raise ValueError("managed agent revision is already active")
            raise ValueError("managed inactive agent revision was not found")
        return max(
            candidates,
            key=lambda item: (item.artifact.installed_at, item.artifact.install_id),
        )

    def _project_record(
        self,
        record: ManagedAgentOnboardingResult,
    ) -> ManagedAgentOnboardingResult:
        current = self._activations.current(record.artifact.local_agent_id)
        activation_id = (
            current[1].activation_id
            if current is not None
            and current[0].install_id == record.artifact.install_id
            else None
        )
        projected = self._reactivation.project(record, activation_id=activation_id)
        return replace(projected, active=activation_id is not None)

    def _inventory(
        self,
        *,
        exclude_local_agent_id: str | None = None,
    ) -> AgentIdentityInventory:
        local = {
            *self._reserved.local_agent_ids,
            *(item.artifact.local_agent_id for item in self._records.records()),
        }
        if exclude_local_agent_id is not None:
            local.discard(exclude_local_agent_id)
        return AgentIdentityInventory(
            core_commands=self._reserved.core_commands,
            native_agent_ids=self._reserved.native_agent_ids,
            native_aliases=self._reserved.native_aliases,
            local_agent_ids=tuple(sorted(local)),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("agent runtime service clock must be timezone-aware")
        return value


def _resolve_unique_entry(
    catalog: ACPRegistryCatalog,
    query: str,
) -> ACPRegistryEntryV1:
    index = ACPRegistryIndex.build(catalog.entries)
    exact = index.entries_by_id.get(query)
    if exact is not None:
        return exact
    matches = index.search(query, limit=2)
    if not matches:
        raise ValueError(f"no ACP Registry agent matches: {query}")
    if len(matches) != 1:
        raise ValueError(f"ACP Registry query is ambiguous: {query}")
    return matches[0]


def _summary(
    record: ManagedAgentOnboardingResult,
    current,
) -> AgentRuntimeSummary:
    active = current is not None and current[0].install_id == record.artifact.install_id
    status = current[1].status.value if active and current is not None else "inactive"
    return AgentRuntimeSummary(
        local_agent_id=record.artifact.local_agent_id,
        registry_id=record.artifact.registry_id,
        version=record.artifact.version,
        install_id=record.artifact.install_id,
        distribution_kind=record.artifact.distribution_kind.value,
        active=active,
        activation_status=status,
        probe_state=record.probe.state.value,
        auth_required=bool(record.probe.auth_methods),
        readiness=project_agent_runtime_readiness(record.probe, active=active),
    )


def _record_matches_lock(
    record: ManagedAgentOnboardingResult,
    lock: AgentLockV1,
) -> bool:
    artifact = record.artifact
    return (
        artifact.registry_id == lock.registry_id
        and artifact.local_agent_id == lock.local_agent_id
        and artifact.version == lock.version
        and artifact.platform == lock.platform
        and record.architecture == lock.architecture
        and artifact.distribution_kind is lock.distribution_kind
        and artifact.artifact_digest == lock.artifact_digest
        and artifact.package_integrity == lock.package_integrity
        and artifact.command == lock.command
        and artifact.arguments == lock.arguments
        and artifact.environment == lock.environment
        and record.receipt.snapshot_digest == lock.snapshot_digest
        and record.receipt.entry_digest == lock.entry_digest
        and record.profile.profile_digest == lock.generated_profile_digest
    )
