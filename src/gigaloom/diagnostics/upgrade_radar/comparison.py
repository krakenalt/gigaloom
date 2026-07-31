"""Pure, deterministic comparison of two sealed-corpus route evaluations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re

from gigaloom.contracts import (
    CompatibilityConfidence,
    CompatibilityStatus,
    OperationalEvidenceStatus,
    ReviewedVersionState,
)
from gigaloom.diagnostics.upgrade_radar.contracts import (
    RouteSnapshotV1,
    SealedCorpusV1,
)
from gigaloom.diagnostics.upgrade_radar.comparison_codec import (
    canonical_digest as _digest,
    comparison_body as _comparison_body,
    evaluation_body as _evaluation_body,
)


MAX_COMPARISON_CONCURRENCY = 8
MAX_EVALUATION_OMISSIONS = 32

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


class DeltaDirection(str, Enum):
    """Direction of one candidate delta relative to the current route."""

    IMPROVED = "improved"
    EQUIVALENT = "equivalent"
    REGRESSED = "regressed"
    INCOMPARABLE = "incomparable"


@dataclass(frozen=True, slots=True)
class ComparisonPolicyV1:
    """Bounded execution and equivalence policy included in comparison evidence."""

    max_concurrency: int = 1
    metric_equivalence_basis_points: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency <= MAX_COMPARISON_CONCURRENCY:
            raise ValueError("upgrade comparison concurrency is out of bounds")
        if not 0 <= self.metric_equivalence_basis_points <= 10_000:
            raise ValueError("upgrade comparison metric tolerance is out of bounds")


@dataclass(frozen=True, slots=True)
class GateObservationV1:
    """Content-free result for one corpus-required behavioral gate."""

    gate_id: str
    status: OperationalEvidenceStatus
    evidence_digest: str

    def __post_init__(self) -> None:
        _validate_identity(self.gate_id, "upgrade gate id")
        if not isinstance(self.status, OperationalEvidenceStatus):
            raise ValueError("upgrade gate status is invalid")
        _validate_digest(self.evidence_digest, "upgrade gate evidence digest")


@dataclass(frozen=True, slots=True)
class CaseObservationV1:
    """Bounded content-free metrics for one sealed corpus case."""

    case_id: str
    gates: tuple[GateObservationV1, ...]
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    known_cost_microunits: int | None
    omissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identity(self.case_id, "upgrade case observation id")
        if (
            not isinstance(self.gates, tuple)
            or not self.gates
            or len(self.gates) > 32
            or any(not isinstance(item, GateObservationV1) for item in self.gates)
        ):
            raise ValueError("upgrade case gates must be a bounded non-empty tuple")
        gates = tuple(sorted(self.gates, key=lambda item: item.gate_id))
        if len({item.gate_id for item in gates}) != len(gates):
            raise ValueError("upgrade case gate ids must be unique")
        object.__setattr__(self, "gates", gates)
        _validate_optional_count(self.latency_ms, "upgrade case latency")
        _validate_optional_count(self.input_tokens, "upgrade case input tokens")
        _validate_optional_count(self.output_tokens, "upgrade case output tokens")
        _validate_optional_count(
            self.known_cost_microunits,
            "upgrade case known cost",
        )
        if (self.input_tokens is None) != (self.output_tokens is None):
            raise ValueError("upgrade case usage must be complete or omitted")
        object.__setattr__(
            self,
            "omissions",
            _normalize_identities(
                self.omissions,
                "upgrade case omissions",
                maximum=MAX_EVALUATION_OMISSIONS,
            ),
        )


@dataclass(frozen=True, slots=True)
class RouteEvaluationV1:
    """One route's complete content-free result over a sealed corpus."""

    route: RouteSnapshotV1
    sealed_corpus_digest: str
    supported_capabilities: tuple[str, ...]
    cases: tuple[CaseObservationV1, ...]
    evaluation_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.route, RouteSnapshotV1):
            raise ValueError("upgrade route evaluation snapshot is invalid")
        _validate_digest(
            self.sealed_corpus_digest,
            "upgrade evaluation corpus digest",
        )
        capabilities = _normalize_identities(
            self.supported_capabilities,
            "upgrade supported capabilities",
            maximum=128,
        )
        required_supported = set(self.route.compatibility.required_capabilities) - set(
            self.route.compatibility.missing_capabilities
        )
        if not required_supported.issubset(capabilities):
            raise ValueError("upgrade supported capabilities contradict route evidence")
        object.__setattr__(self, "supported_capabilities", capabilities)
        if (
            not isinstance(self.cases, tuple)
            or not self.cases
            or len(self.cases) > 32
            or any(not isinstance(item, CaseObservationV1) for item in self.cases)
        ):
            raise ValueError("upgrade evaluation cases must be bounded and non-empty")
        cases = tuple(sorted(self.cases, key=lambda item: item.case_id))
        if len({item.case_id for item in cases}) != len(cases):
            raise ValueError("upgrade evaluation case ids must be unique")
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "evaluation_digest", _digest(_evaluation_body(self)))


@dataclass(frozen=True, slots=True)
class CapabilityDeltaV1:
    """Candidate capability gains and losses."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    new_required_losses: tuple[str, ...]
    resolved_required_losses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompatibilityDeltaV1:
    """Protocol-first compatibility relationship between route revisions."""

    current_status: CompatibilityStatus
    candidate_status: CompatibilityStatus
    current_confidence: CompatibilityConfidence
    candidate_confidence: CompatibilityConfidence
    current_reviewed_version: ReviewedVersionState
    candidate_reviewed_version: ReviewedVersionState
    protocol_family_match: bool
    protocol_version_match: bool


@dataclass(frozen=True, slots=True)
class GateDeltaV1:
    """Candidate behavioral gate result relative to the current route."""

    case_id: str
    gate_id: str
    current_status: OperationalEvidenceStatus
    candidate_status: OperationalEvidenceStatus
    direction: DeltaDirection
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class MetricDeltaV1:
    """Normalized lower-is-better metric delta over the same sealed corpus."""

    metric_id: str
    current_value: int | None
    candidate_value: int | None
    delta_basis_points: int | None
    direction: DeltaDirection
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class UpgradeComparisonV1:
    """Immutable deterministic comparison input to recommendation policy."""

    sealed_corpus_digest: str
    current_evaluation_digest: str
    candidate_evaluation_digest: str
    policy: ComparisonPolicyV1
    compatibility_delta: CompatibilityDeltaV1
    capability_delta: CapabilityDeltaV1
    gate_deltas: tuple[GateDeltaV1, ...]
    metric_deltas: tuple[MetricDeltaV1, ...]
    uncertainty: tuple[str, ...]
    omissions: tuple[str, ...]
    comparison_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.sealed_corpus_digest, "upgrade comparison corpus digest"),
            (self.current_evaluation_digest, "current evaluation digest"),
            (self.candidate_evaluation_digest, "candidate evaluation digest"),
        ):
            _validate_digest(value, field_name)
        if not isinstance(self.policy, ComparisonPolicyV1):
            raise ValueError("upgrade comparison policy is invalid")
        if not isinstance(self.compatibility_delta, CompatibilityDeltaV1):
            raise ValueError("upgrade compatibility delta is invalid")
        if not isinstance(self.capability_delta, CapabilityDeltaV1):
            raise ValueError("upgrade capability delta is invalid")
        object.__setattr__(
            self,
            "uncertainty",
            _normalize_identities(
                self.uncertainty,
                "upgrade comparison uncertainty",
                maximum=64,
            ),
        )
        object.__setattr__(
            self,
            "omissions",
            _normalize_identities(
                self.omissions,
                "upgrade comparison omissions",
                maximum=64,
            ),
        )
        object.__setattr__(self, "comparison_digest", _digest(_comparison_body(self)))

    @property
    def candidate_required_gates_pass(self) -> bool:
        """Return true only when every candidate gate has passed."""
        return bool(self.gate_deltas) and all(
            item.candidate_status is OperationalEvidenceStatus.PASSED
            for item in self.gate_deltas
        )

    @property
    def candidate_has_regression(self) -> bool:
        """Return whether capability, behavioral, or metric evidence regressed."""
        return bool(
            self.capability_delta.new_required_losses
            or any(
                item.direction is DeltaDirection.REGRESSED for item in self.gate_deltas
            )
            or any(
                item.direction is DeltaDirection.REGRESSED
                for item in self.metric_deltas
            )
        )


def compare_route_evaluations(
    corpus: SealedCorpusV1,
    current: RouteEvaluationV1,
    candidate: RouteEvaluationV1,
    *,
    policy: ComparisonPolicyV1 | None = None,
) -> UpgradeComparisonV1:
    """Compare two complete evaluations without I/O, execution, or fallback."""
    if not isinstance(corpus, SealedCorpusV1):
        raise ValueError("upgrade comparison corpus is invalid")
    if current.sealed_corpus_digest != corpus.sealed_digest or (
        candidate.sealed_corpus_digest != corpus.sealed_digest
    ):
        raise ValueError("upgrade evaluations do not match the sealed corpus")
    if current.route.snapshot_digest == candidate.route.snapshot_digest:
        raise ValueError("upgrade comparison requires distinct route snapshots")
    _validate_case_coverage(corpus, current)
    _validate_case_coverage(corpus, candidate)
    active_policy = policy or ComparisonPolicyV1()
    current_capabilities = set(current.supported_capabilities)
    candidate_capabilities = set(candidate.supported_capabilities)
    current_missing = set(current.route.compatibility.missing_capabilities)
    candidate_missing = set(candidate.route.compatibility.missing_capabilities)
    capability_delta = CapabilityDeltaV1(
        added=tuple(sorted(candidate_capabilities - current_capabilities)),
        removed=tuple(sorted(current_capabilities - candidate_capabilities)),
        new_required_losses=tuple(sorted(candidate_missing - current_missing)),
        resolved_required_losses=tuple(sorted(current_missing - candidate_missing)),
    )
    current_compatibility = current.route.compatibility
    candidate_compatibility = candidate.route.compatibility
    compatibility_delta = CompatibilityDeltaV1(
        current_status=current_compatibility.status,
        candidate_status=candidate_compatibility.status,
        current_confidence=current_compatibility.confidence,
        candidate_confidence=candidate_compatibility.confidence,
        current_reviewed_version=current_compatibility.reviewed_version.state,
        candidate_reviewed_version=candidate_compatibility.reviewed_version.state,
        protocol_family_match=(
            current_compatibility.protocol_family
            == candidate_compatibility.protocol_family
        ),
        protocol_version_match=(
            current_compatibility.protocol_version
            == candidate_compatibility.protocol_version
        ),
    )
    gate_deltas = _compare_gates(current, candidate)
    metric_deltas = tuple(
        _metric_delta(metric_id, current_value, candidate_value, active_policy)
        for metric_id, current_value, candidate_value in (
            (
                "latency_total_ms",
                _total(current.cases, "latency_ms"),
                _total(candidate.cases, "latency_ms"),
            ),
            (
                "usage_total_tokens",
                _usage_total(current.cases),
                _usage_total(candidate.cases),
            ),
            (
                "known_cost_total_microunits",
                _total(current.cases, "known_cost_microunits"),
                _total(candidate.cases, "known_cost_microunits"),
            ),
        )
    )
    uncertainty = set()
    for metric in metric_deltas:
        if metric.direction is DeltaDirection.INCOMPARABLE:
            uncertainty.add(f"{metric.metric_id}_unknown")
    for gate in gate_deltas:
        if gate.direction is DeltaDirection.INCOMPARABLE:
            uncertainty.add(f"gate_unknown:{gate.case_id}:{gate.gate_id}")
    for label, evaluation in (("current", current), ("candidate", candidate)):
        if evaluation.route.compatibility.status.value == "compatible_unverified":
            uncertainty.add(f"{label}_compatibility_unverified")
    omissions = {
        f"{label}:{case.case_id}:{omission}"
        for label, evaluation in (("current", current), ("candidate", candidate))
        for case in evaluation.cases
        for omission in case.omissions
    }
    return UpgradeComparisonV1(
        sealed_corpus_digest=corpus.sealed_digest,
        current_evaluation_digest=current.evaluation_digest,
        candidate_evaluation_digest=candidate.evaluation_digest,
        policy=active_policy,
        compatibility_delta=compatibility_delta,
        capability_delta=capability_delta,
        gate_deltas=gate_deltas,
        metric_deltas=metric_deltas,
        uncertainty=tuple(uncertainty),
        omissions=tuple(omissions),
    )


def _validate_case_coverage(
    corpus: SealedCorpusV1,
    evaluation: RouteEvaluationV1,
) -> None:
    expected_cases = {item.case_id: item for item in corpus.cases}
    observed_cases = {item.case_id: item for item in evaluation.cases}
    if set(observed_cases) != set(expected_cases):
        raise ValueError("upgrade evaluation case coverage is incomplete")
    for case_id, expected in expected_cases.items():
        observed_gates = {item.gate_id for item in observed_cases[case_id].gates}
        if observed_gates != set(expected.required_gates):
            raise ValueError("upgrade evaluation gate coverage is incomplete")


def _compare_gates(
    current: RouteEvaluationV1,
    candidate: RouteEvaluationV1,
) -> tuple[GateDeltaV1, ...]:
    current_cases = {item.case_id: item for item in current.cases}
    deltas: list[GateDeltaV1] = []
    for candidate_case in candidate.cases:
        current_gates = {
            item.gate_id: item for item in current_cases[candidate_case.case_id].gates
        }
        for candidate_gate in candidate_case.gates:
            current_gate = current_gates[candidate_gate.gate_id]
            direction = _gate_direction(current_gate.status, candidate_gate.status)
            deltas.append(
                GateDeltaV1(
                    case_id=candidate_case.case_id,
                    gate_id=candidate_gate.gate_id,
                    current_status=current_gate.status,
                    candidate_status=candidate_gate.status,
                    direction=direction,
                    evidence_digest=_digest(
                        {
                            "case_id": candidate_case.case_id,
                            "gate_id": candidate_gate.gate_id,
                            "current": current_gate.status.value,
                            "current_evidence": current_gate.evidence_digest,
                            "candidate": candidate_gate.status.value,
                            "candidate_evidence": candidate_gate.evidence_digest,
                        }
                    ),
                )
            )
    return tuple(deltas)


def _gate_direction(
    current: OperationalEvidenceStatus,
    candidate: OperationalEvidenceStatus,
) -> DeltaDirection:
    if candidate in {
        OperationalEvidenceStatus.UNKNOWN,
        OperationalEvidenceStatus.SKIPPED,
    }:
        return DeltaDirection.INCOMPARABLE
    if current is OperationalEvidenceStatus.PASSED and (
        candidate is not OperationalEvidenceStatus.PASSED
    ):
        return DeltaDirection.REGRESSED
    if current is not OperationalEvidenceStatus.PASSED and (
        candidate is OperationalEvidenceStatus.PASSED
    ):
        return DeltaDirection.IMPROVED
    return DeltaDirection.EQUIVALENT


def _metric_delta(
    metric_id: str,
    current: int | None,
    candidate: int | None,
    policy: ComparisonPolicyV1,
) -> MetricDeltaV1:
    delta_basis_points: int | None = None
    if current is None or candidate is None:
        direction = DeltaDirection.INCOMPARABLE
    elif current == 0:
        if candidate == 0:
            delta_basis_points = 0
            direction = DeltaDirection.EQUIVALENT
        else:
            direction = DeltaDirection.REGRESSED
    else:
        delta_basis_points = ((candidate - current) * 10_000) // current
        if abs(delta_basis_points) <= policy.metric_equivalence_basis_points:
            direction = DeltaDirection.EQUIVALENT
        elif delta_basis_points < 0:
            direction = DeltaDirection.IMPROVED
        else:
            direction = DeltaDirection.REGRESSED
    evidence_digest = _digest(
        {
            "metric_id": metric_id,
            "current_value": current,
            "candidate_value": candidate,
            "delta_basis_points": delta_basis_points,
            "direction": direction.value,
            "tolerance_basis_points": policy.metric_equivalence_basis_points,
        }
    )
    return MetricDeltaV1(
        metric_id=metric_id,
        current_value=current,
        candidate_value=candidate,
        delta_basis_points=delta_basis_points,
        direction=direction,
        evidence_digest=evidence_digest,
    )


def _usage_total(cases: tuple[CaseObservationV1, ...]) -> int | None:
    if any(item.input_tokens is None or item.output_tokens is None for item in cases):
        return None
    return sum((item.input_tokens or 0) + (item.output_tokens or 0) for item in cases)


def _total(cases: tuple[CaseObservationV1, ...], field_name: str) -> int | None:
    values = [getattr(item, field_name) for item in cases]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if isinstance(value, int))


def _normalize_identities(
    values: object,
    field_name: str,
    *,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError(f"{field_name} must be a bounded tuple")
    normalized = tuple(_validate_identity(item, field_name) for item in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must be unique")
    return tuple(sorted(normalized))


def _validate_identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def _validate_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")
    return value


def _validate_optional_count(value: object, field_name: str) -> None:
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value < 0
    ):
        raise ValueError(f"{field_name} must be a non-negative integer or null")


__all__ = [
    "CaseObservationV1",
    "CapabilityDeltaV1",
    "CompatibilityDeltaV1",
    "ComparisonPolicyV1",
    "DeltaDirection",
    "GateDeltaV1",
    "GateObservationV1",
    "MetricDeltaV1",
    "RouteEvaluationV1",
    "UpgradeComparisonV1",
    "compare_route_evaluations",
]
