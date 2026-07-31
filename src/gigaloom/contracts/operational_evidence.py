"""Content-free recovery and upgrade-radar report contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, cast, runtime_checkable

from gigaloom.contracts.compatibility import CompatibilityObservationV1
from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    normalize_identities,
    validate_digest,
    validate_identity,
    validate_optional_digest,
    validate_schema_version,
    validate_time_range,
)


OPERATIONAL_EVIDENCE_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION


class OperationalEvidenceStatus(str, Enum):
    """Stable result state for bounded content-free evidence."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class UpgradeRecommendation(str, Enum):
    """Recommendation-only outcome; never automatic promotion authority."""

    RETAIN_CURRENT = "retain_current"
    PROMOTE_CANDIDATE = "promote_candidate"
    NEEDS_HUMAN = "needs_human"
    INCOMPARABLE = "incomparable"


@dataclass(frozen=True, slots=True)
class OperationalEvidenceV1:
    """One bounded content-free evidence reference."""

    evidence_id: str
    kind: str
    status: OperationalEvidenceStatus
    evidence_digest: str
    reason_code: str
    source_digest: str | None = None
    schema_version: int = OPERATIONAL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="operational evidence")
        validate_identity(self.evidence_id, field_name="operational evidence id")
        validate_identity(self.kind, field_name="operational evidence kind")
        if not isinstance(self.status, OperationalEvidenceStatus):
            raise ValueError("operational evidence status is invalid")
        validate_digest(self.evidence_digest, field_name="operational evidence digest")
        validate_identity(self.reason_code, field_name="operational evidence reason")
        validate_optional_digest(
            self.source_digest,
            field_name="operational evidence source digest",
        )


@dataclass(frozen=True, slots=True)
class RouteEvidenceV1:
    """Immutable route revision and negotiated compatibility binding."""

    route_id: str
    revision_digest: str
    capability_fingerprint: str
    compatibility_observation_digest: str
    schema_version: int = OPERATIONAL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="route evidence")
        validate_identity(self.route_id, field_name="route evidence id")
        validate_digest(self.revision_digest, field_name="route revision digest")
        validate_digest(
            self.capability_fingerprint,
            field_name="route capability fingerprint",
        )
        validate_digest(
            self.compatibility_observation_digest,
            field_name="route compatibility observation digest",
        )


@dataclass(frozen=True, slots=True)
class RecoveryReceiptV1:
    """Content-free bounded recovery and fault-lab evidence."""

    receipt_id: str
    data_root_fingerprint: str
    check_catalog_digest: str
    started_at: datetime
    finished_at: datetime
    checks: tuple[OperationalEvidenceV1, ...]
    derived_rebuilds: tuple[OperationalEvidenceV1, ...]
    quarantine_previews: tuple[OperationalEvidenceV1, ...]
    fault_fixture_ids: tuple[str, ...]
    invariants: tuple[OperationalEvidenceV1, ...]
    omissions: tuple[str, ...]
    content_free: bool = True
    schema_version: int = OPERATIONAL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="recovery receipt")
        validate_identity(self.receipt_id, field_name="recovery receipt id")
        validate_digest(
            self.data_root_fingerprint,
            field_name="recovery data root fingerprint",
        )
        validate_digest(
            self.check_catalog_digest,
            field_name="recovery check catalog digest",
        )
        validate_time_range(
            self.started_at,
            self.finished_at,
            field_name="recovery receipt",
        )
        for field_name in (
            "checks",
            "derived_rebuilds",
            "quarantine_previews",
            "invariants",
        ):
            value = _normalize_evidence(
                getattr(self, field_name),
                field_name=f"recovery {field_name}",
            )
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "fault_fixture_ids",
            normalize_identities(
                self.fault_fixture_ids,
                field_name="recovery fault fixture ids",
            ),
        )
        object.__setattr__(
            self,
            "omissions",
            normalize_identities(
                self.omissions,
                field_name="recovery omissions",
            ),
        )
        if self.content_free is not True:
            raise ValueError("recovery receipt must be content-free")


@dataclass(frozen=True, slots=True)
class UpgradeRadarReportV1:
    """Immutable comparison evidence with recommendation-only semantics."""

    report_id: str
    sealed_corpus_digest: str
    current_route: RouteEvidenceV1
    candidate_route: RouteEvidenceV1
    compatibility_observations: tuple[CompatibilityObservationV1, ...]
    capability_delta: tuple[OperationalEvidenceV1, ...]
    loss_delta: tuple[OperationalEvidenceV1, ...]
    gate_results: tuple[OperationalEvidenceV1, ...]
    latency_observations: tuple[OperationalEvidenceV1, ...]
    usage_observations: tuple[OperationalEvidenceV1, ...]
    cost_observations: tuple[OperationalEvidenceV1, ...]
    uncertainty: tuple[str, ...]
    omissions: tuple[str, ...]
    recommendation: UpgradeRecommendation
    content_free: bool = True
    schema_version: int = OPERATIONAL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="upgrade radar report")
        validate_identity(self.report_id, field_name="upgrade radar report id")
        validate_digest(
            self.sealed_corpus_digest,
            field_name="upgrade radar sealed corpus digest",
        )
        if not isinstance(self.current_route, RouteEvidenceV1) or not isinstance(
            self.candidate_route,
            RouteEvidenceV1,
        ):
            raise ValueError("upgrade radar routes are invalid")
        if (
            self.current_route.route_id == self.candidate_route.route_id
            and self.current_route.revision_digest
            == self.candidate_route.revision_digest
        ):
            raise ValueError("upgrade radar route revisions must be distinct")
        observations = _normalize_compatibility(self.compatibility_observations)
        object.__setattr__(self, "compatibility_observations", observations)
        observations_by_digest = {item.probe_digest: item for item in observations}
        for route in (self.current_route, self.candidate_route):
            observation = observations_by_digest.get(
                route.compatibility_observation_digest
            )
            if (
                observation is None
                or observation.route_id != route.route_id
                or observation.capability_fingerprint != route.capability_fingerprint
            ):
                raise ValueError(
                    "upgrade radar route compatibility evidence does not match"
                )
        for field_name in (
            "capability_delta",
            "loss_delta",
            "gate_results",
            "latency_observations",
            "usage_observations",
            "cost_observations",
        ):
            value = _normalize_evidence(
                getattr(self, field_name),
                field_name=f"upgrade radar {field_name}",
            )
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "uncertainty",
            normalize_identities(
                self.uncertainty,
                field_name="upgrade radar uncertainty",
            ),
        )
        object.__setattr__(
            self,
            "omissions",
            normalize_identities(
                self.omissions,
                field_name="upgrade radar omissions",
            ),
        )
        if not isinstance(self.recommendation, UpgradeRecommendation):
            raise ValueError("upgrade radar recommendation is invalid")
        if not self.gate_results:
            raise ValueError("upgrade radar requires candidate gate evidence")
        if self.recommendation is UpgradeRecommendation.PROMOTE_CANDIDATE and any(
            result.status is not OperationalEvidenceStatus.PASSED
            for result in self.gate_results
        ):
            raise ValueError("non-passing candidate gate cannot recommend promotion")
        if self.content_free is not True:
            raise ValueError("upgrade radar report must be content-free")


@runtime_checkable
class RecoveryReceiptPort(Protocol):
    """Public persistence boundary for immutable recovery receipts."""

    def save(self, receipt: RecoveryReceiptV1) -> None:
        """Persist one validated recovery receipt."""


@runtime_checkable
class UpgradeRadarReportPort(Protocol):
    """Public persistence boundary for recommendation-only reports."""

    def save(self, report: UpgradeRadarReportV1) -> None:
        """Persist one validated immutable report."""


def _normalize_evidence(
    values: object,
    *,
    field_name: str,
) -> tuple[OperationalEvidenceV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > 256
        or any(not isinstance(item, OperationalEvidenceV1) for item in values)
    ):
        raise ValueError(f"{field_name} must be a bounded evidence tuple")
    typed = cast(tuple[OperationalEvidenceV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.evidence_id))
    ids = [item.evidence_id for item in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{field_name} ids must be unique")
    return normalized


def _normalize_compatibility(
    values: object,
) -> tuple[CompatibilityObservationV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) != 2
        or any(not isinstance(item, CompatibilityObservationV1) for item in values)
    ):
        raise ValueError("upgrade radar requires two compatibility observations")
    typed = cast(tuple[CompatibilityObservationV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.observation_id))
    if normalized[0].observation_id == normalized[1].observation_id:
        raise ValueError("upgrade radar observations must be distinct")
    return normalized
