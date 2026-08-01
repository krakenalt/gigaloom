"""Pure policy-driven ACP Registry distribution and identity planner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re

from gigaloom.contracts import (
    ACPDistributionKind,
    ACPDistributionV1,
    ACPRegistryEntryV1,
    ACPRegistrySnapshotV1,
    AgentInstallPlanV1,
    AgentIntegrityPolicy,
    AgentLifecycleScriptPolicy,
)
from gigaloom.contracts.operational_validation import (
    canonical_digest,
    normalize_identities,
    validate_absolute_path,
    validate_identity,
)
from gigaloom.harnesses.agent_profiles.installations.models import (
    DistributionDecision,
    DistributionResolutionV1,
    InstallPlanningResult,
    InstallSelectionStatus,
)


_NPX_EXACT_RE = re.compile(
    r"(?P<name>(?:@[a-z0-9._~-]+/)?[a-z0-9._~-]+)@"
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\Z"
)
_UVX_EXACT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?=="
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\Z"
)


@dataclass(frozen=True, slots=True)
class AgentIdentityInventory:
    """Bounded namespace inventory that prevents command/profile shadowing."""

    core_commands: tuple[str, ...] = ()
    native_agent_ids: tuple[str, ...] = ()
    native_aliases: tuple[str, ...] = ()
    local_agent_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "core_commands",
            "native_agent_ids",
            "native_aliases",
            "local_agent_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                normalize_identities(
                    getattr(self, field_name),
                    field_name=field_name.replace("_", " "),
                ),
            )

    def collision_namespaces(self, identity: str) -> tuple[str, ...]:
        """Return every namespace already owning one candidate identity."""
        validate_identity(identity, field_name="candidate local agent id")
        matches = []
        for namespace, values in (
            ("core_command", self.core_commands),
            ("native_agent", self.native_agent_ids),
            ("native_alias", self.native_aliases),
            ("local_agent", self.local_agent_ids),
        ):
            if identity in values:
                matches.append(namespace)
        return tuple(matches)

    def first_available_suffix(self, registry_id: str) -> str:
        """Return a stable non-shadowing ACP suffix proposal."""
        validate_identity(registry_id, field_name="ACP registry id")
        for index in range(1, 101):
            suffix = "-acp" if index == 1 else f"-acp-{index}"
            candidate = f"{registry_id}{suffix}"
            if not self.collision_namespaces(candidate):
                return candidate
        raise ValueError("no bounded ACP identity suffix is available")


@dataclass(frozen=True, slots=True)
class AgentIdentityPlan:
    """Safe local identity decision, including unresolved collision evidence."""

    local_agent_id: str | None
    proposed_local_agent_id: str | None
    collision_namespaces: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentInstallPlannerPolicy:
    """Host and managed-root policy supplied explicitly by the application."""

    platform: str
    architecture: str
    data_root: str
    plan_ttl: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        validate_identity(self.platform, field_name="planner platform")
        validate_identity(self.architecture, field_name="planner architecture")
        validate_absolute_path(self.data_root, field_name="planner data root")
        if not timedelta(seconds=1) <= self.plan_ttl <= timedelta(hours=1):
            raise ValueError("planner TTL is outside the supported bound")


class AgentInstallPlanner:
    """Select one exact distribution without network, import, or execution."""

    def __init__(self, policy: AgentInstallPlannerPolicy) -> None:
        self._policy = policy

    def plan(
        self,
        entry: ACPRegistryEntryV1,
        snapshot: ACPRegistrySnapshotV1,
        *,
        inventory: AgentIdentityInventory = AgentIdentityInventory(),
        local_agent_id: str | None = None,
        resolutions: tuple[DistributionResolutionV1, ...] = (),
        now: datetime,
    ) -> InstallPlanningResult:
        """Return an immutable plan or a fail-closed, fully explained outcome."""
        self._validate_inputs(entry, snapshot, resolutions, now)
        identity = self._plan_identity(entry.registry_id, inventory, local_agent_id)
        evaluations = self._evaluate(entry, resolutions)
        if identity.local_agent_id is None:
            return InstallPlanningResult(
                plan=None,
                decisions=tuple(
                    DistributionDecision(
                        distribution_digest=item.distribution_digest,
                        rank=rank,
                        status=InstallSelectionStatus.REJECTED,
                        reason_code="identity_collision_requires_explicit_alias",
                    )
                    for rank, (_, item, _, _) in enumerate(evaluations)
                ),
                local_agent_id=None,
                proposed_local_agent_id=identity.proposed_local_agent_id,
                collision_namespaces=identity.collision_namespaces,
                reason_code="identity_collision_requires_explicit_alias",
            )
        selected_position = next(
            (index for index, value in enumerate(evaluations) if value[0] is None),
            None,
        )
        if selected_position is None:
            return InstallPlanningResult(
                plan=None,
                decisions=tuple(
                    DistributionDecision(
                        distribution_digest=item.distribution_digest,
                        rank=rank,
                        status=InstallSelectionStatus.REJECTED,
                        reason_code=reason or "distribution_not_admissible",
                    )
                    for rank, (reason, item, _, _) in enumerate(evaluations)
                ),
                local_agent_id=identity.local_agent_id,
                proposed_local_agent_id=None,
                collision_namespaces=(),
                reason_code="no_admissible_distribution",
            )
        _, selected, resolution, selection_rank = evaluations[selected_position]
        decisions = tuple(
            DistributionDecision(
                distribution_digest=item.distribution_digest,
                rank=rank,
                status=(
                    InstallSelectionStatus.SELECTED
                    if index == selected_position
                    else InstallSelectionStatus.REJECTED
                ),
                reason_code=(
                    "selected_by_policy"
                    if index == selected_position
                    else reason or "lower_policy_preference"
                ),
            )
            for index, (reason, item, _, rank) in enumerate(evaluations)
        )
        plan = self._build_plan(
            entry,
            snapshot,
            selected,
            resolution,
            identity.local_agent_id,
            selection_rank,
            now,
        )
        return InstallPlanningResult(
            plan=plan,
            decisions=decisions,
            local_agent_id=identity.local_agent_id,
            proposed_local_agent_id=None,
            collision_namespaces=(),
            reason_code="distribution_selected",
        )

    def _evaluate(
        self,
        entry: ACPRegistryEntryV1,
        resolutions: tuple[DistributionResolutionV1, ...],
    ) -> tuple[
        tuple[str | None, ACPDistributionV1, DistributionResolutionV1 | None, int], ...
    ]:
        by_digest = {item.distribution_digest: item for item in resolutions}
        values = []
        for item in entry.distributions:
            resolution = by_digest.get(item.distribution_digest)
            reason, rank = self._admission(item, entry.version, resolution)
            values.append((reason, item, resolution, rank))
        return tuple(
            sorted(values, key=lambda value: (value[3], value[1].distribution_digest))
        )

    def _admission(
        self,
        distribution: ACPDistributionV1,
        version: str,
        resolution: DistributionResolutionV1 | None,
    ) -> tuple[str | None, int]:
        if distribution.kind is ACPDistributionKind.BINARY:
            if (
                distribution.platform != self._policy.platform
                or distribution.architecture != self._policy.architecture
            ):
                return "platform_or_architecture_mismatch", 0
            if distribution.expected_integrity is not None:
                return None, 0
            return None, 3
        if distribution.kind is ACPDistributionKind.NPX:
            if not _is_exact_package(
                distribution.package_or_archive, version, npx=True
            ):
                return "npx_version_not_exact", 1
            if resolution is None or resolution.package_integrity is None:
                return "npm_integrity_resolution_required", 1
            return None, 1
        if not _is_exact_package(distribution.package_or_archive, version, npx=False):
            return "uvx_version_not_exact", 2
        if (
            resolution is None
            or resolution.lock_digest is None
            or resolution.interpreter_fingerprint is None
        ):
            return "uvx_lock_resolution_required", 2
        return None, 2

    def _build_plan(
        self,
        entry: ACPRegistryEntryV1,
        snapshot: ACPRegistrySnapshotV1,
        distribution: ACPDistributionV1,
        resolution: DistributionResolutionV1 | None,
        local_agent_id: str,
        selection_rank: int,
        now: datetime,
    ) -> AgentInstallPlanV1:
        artifact_digest = (
            resolution.artifact_digest
            if resolution is not None
            else distribution.expected_integrity or distribution.distribution_digest
        )
        expected = (
            resolution.package_integrity
            if resolution is not None and resolution.package_integrity is not None
            else (
                resolution.artifact_digest
                if resolution is not None
                else distribution.expected_integrity
            )
        )
        expires_at = now + self._policy.plan_ttl
        plan_digest = canonical_digest(
            {
                "entry_digest": entry.entry_digest,
                "snapshot_digest": snapshot.snapshot_digest,
                "local_agent_id": local_agent_id,
                "distribution_digest": distribution.distribution_digest,
                "artifact_digest": artifact_digest,
                "selection_rank": selection_rank,
                "expires_at": expires_at.isoformat(),
            }
        )
        plan_id = f"plan-{plan_digest[:24]}"
        root = Path(self._policy.data_root)
        staging_root = root / "agents" / "staging" / plan_id
        managed_root = (
            root
            / "agents"
            / "registry"
            / local_agent_id
            / entry.version
            / f"{self._policy.platform}-{self._policy.architecture}"
            / artifact_digest
        )
        unverified = distribution.expected_integrity is None and resolution is None
        return AgentInstallPlanV1(
            plan_id=plan_id,
            registry_id=entry.registry_id,
            entry_digest=entry.entry_digest,
            snapshot_digest=snapshot.snapshot_digest,
            local_agent_id=local_agent_id,
            version=entry.version,
            platform=self._policy.platform,
            architecture=self._policy.architecture,
            distribution_kind=distribution.kind,
            source=distribution.source,
            package_or_archive=distribution.package_or_archive,
            expected_integrity=expected,
            command=distribution.command,
            arguments=distribution.arguments,
            environment=distribution.environment,
            network_origins=distribution.network_origins,
            staging_root=str(staging_root),
            managed_root=str(managed_root),
            integrity_policy=(
                AgentIntegrityPolicy.ALLOW_EXPLICIT_UNVERIFIED
                if unverified
                else AgentIntegrityPolicy.REQUIRE_VERIFIED
            ),
            lifecycle_script_policy=AgentLifecycleScriptPolicy.DISABLED,
            side_effects=_side_effects(distribution.kind, unverified=unverified),
            confirmation_required=True,
            expires_at=expires_at,
        )

    @staticmethod
    def _plan_identity(
        registry_id: str,
        inventory: AgentIdentityInventory,
        local_agent_id: str | None,
    ) -> AgentIdentityPlan:
        candidate = local_agent_id or registry_id
        validate_identity(candidate, field_name="local agent id")
        collisions = inventory.collision_namespaces(candidate)
        if collisions:
            return AgentIdentityPlan(
                local_agent_id=None,
                proposed_local_agent_id=inventory.first_available_suffix(registry_id),
                collision_namespaces=collisions,
            )
        return AgentIdentityPlan(
            local_agent_id=candidate,
            proposed_local_agent_id=None,
            collision_namespaces=(),
        )

    @staticmethod
    def _validate_inputs(
        entry: ACPRegistryEntryV1,
        snapshot: ACPRegistrySnapshotV1,
        resolutions: tuple[DistributionResolutionV1, ...],
        now: datetime,
    ) -> None:
        if not isinstance(entry, ACPRegistryEntryV1) or not isinstance(
            snapshot, ACPRegistrySnapshotV1
        ):
            raise ValueError("planner requires validated registry contracts")
        if entry.snapshot_digest != snapshot.snapshot_digest:
            raise ValueError("registry entry is not bound to the selected snapshot")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("planner time must be timezone-aware")
        if not isinstance(resolutions, tuple) or any(
            not isinstance(item, DistributionResolutionV1) for item in resolutions
        ):
            raise ValueError("distribution resolutions must be an immutable tuple")
        digests = tuple(item.distribution_digest for item in resolutions)
        if len(set(digests)) != len(digests):
            raise ValueError("distribution resolutions must be unique")
        known = {item.distribution_digest for item in entry.distributions}
        if not set(digests) <= known:
            raise ValueError("distribution resolution is not bound to this entry")


def _is_exact_package(package: str, version: str, *, npx: bool) -> bool:
    match = (_NPX_EXACT_RE if npx else _UVX_EXACT_RE).fullmatch(package)
    return match is not None and match.group("version") == version


def _side_effects(
    kind: ACPDistributionKind,
    *,
    unverified: bool,
) -> tuple[str, ...]:
    if kind is ACPDistributionKind.BINARY:
        base = ("bounded_https_download", "private_archive_extraction")
    elif kind is ACPDistributionKind.NPX:
        base = ("private_npm_resolution", "private_npm_prefix_install")
    else:
        base = ("private_uv_resolution", "private_uv_environment_install")
    if unverified:
        base = (*base, "explicit_unverified_artifact_admission")
    return (*base, "managed_artifact_creation")
