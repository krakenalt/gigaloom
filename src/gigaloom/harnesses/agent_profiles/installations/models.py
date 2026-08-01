"""Immutable explanation models for ACP agent installation planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gigaloom.contracts import AgentInstallPlanV1
from gigaloom.contracts.operational_validation import (
    normalize_identities,
    validate_digest,
    validate_identity,
    validate_optional_digest,
    validate_optional_integrity,
)


class InstallSelectionStatus(str, Enum):
    """Admission outcome for one inert registry distribution."""

    SELECTED = "selected"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DistributionResolutionV1:
    """Package-manager evidence resolved outside the pure planner."""

    distribution_digest: str
    artifact_digest: str
    package_integrity: str | None = None
    lock_digest: str | None = None
    interpreter_fingerprint: str | None = None

    def __post_init__(self) -> None:
        validate_digest(
            self.distribution_digest,
            field_name="resolved distribution digest",
        )
        validate_digest(self.artifact_digest, field_name="resolved artifact digest")
        validate_optional_integrity(
            self.package_integrity,
            field_name="resolved package integrity",
        )
        validate_optional_digest(self.lock_digest, field_name="resolved lock digest")
        validate_optional_digest(
            self.interpreter_fingerprint,
            field_name="resolved interpreter fingerprint",
        )


@dataclass(frozen=True, slots=True)
class DistributionDecision:
    """One deterministic selected/rejected distribution explanation."""

    distribution_digest: str
    rank: int
    status: InstallSelectionStatus
    reason_code: str

    def __post_init__(self) -> None:
        validate_digest(self.distribution_digest, field_name="distribution digest")
        if (
            isinstance(self.rank, bool)
            or not isinstance(self.rank, int)
            or not 0 <= self.rank <= 10_000
        ):
            raise ValueError("distribution decision rank is invalid")
        if not isinstance(self.status, InstallSelectionStatus):
            raise ValueError("distribution decision status is invalid")
        validate_identity(self.reason_code, field_name="distribution decision reason")


@dataclass(frozen=True, slots=True)
class InstallPlanningResult:
    """Pure plan outcome with complete alternative and identity evidence."""

    plan: AgentInstallPlanV1 | None
    decisions: tuple[DistributionDecision, ...]
    local_agent_id: str | None
    proposed_local_agent_id: str | None
    collision_namespaces: tuple[str, ...]
    reason_code: str

    def __post_init__(self) -> None:
        if self.plan is not None and not isinstance(self.plan, AgentInstallPlanV1):
            raise ValueError("installation plan result has an invalid plan")
        if (
            not isinstance(self.decisions, tuple)
            or not self.decisions
            or any(
                not isinstance(item, DistributionDecision) for item in self.decisions
            )
        ):
            raise ValueError("installation plan decisions are invalid")
        digests = tuple(item.distribution_digest for item in self.decisions)
        if len(set(digests)) != len(digests):
            raise ValueError("installation plan decisions must be unique")
        selected = tuple(
            item
            for item in self.decisions
            if item.status is InstallSelectionStatus.SELECTED
        )
        if (self.plan is None and selected) or (
            self.plan is not None and len(selected) != 1
        ):
            raise ValueError("installation plan selection evidence is inconsistent")
        for value, label in (
            (self.local_agent_id, "planned local agent id"),
            (self.proposed_local_agent_id, "proposed local agent id"),
        ):
            if value is not None:
                validate_identity(value, field_name=label)
        object.__setattr__(
            self,
            "collision_namespaces",
            normalize_identities(
                self.collision_namespaces,
                field_name="identity collision namespaces",
            ),
        )
        validate_identity(self.reason_code, field_name="install planning reason")
