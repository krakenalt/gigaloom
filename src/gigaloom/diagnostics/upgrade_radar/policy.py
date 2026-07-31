"""Deterministic recommendation-only policy for upgrade comparisons."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from gigaloom.contracts import (
    CompatibilityStatus,
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
    UpgradeRadarReportV1,
    UpgradeRecommendation,
)
from gigaloom.diagnostics.upgrade_radar.comparison import (
    DeltaDirection,
    RouteEvaluationV1,
    UpgradeComparisonV1,
)
from gigaloom.diagnostics.upgrade_radar.contracts import SealedCorpusV1


@dataclass(frozen=True, slots=True)
class RecommendationPolicyV1:
    """Explicit switches for evidence completeness and unreviewed candidates."""

    allow_compatible_unverified: bool = False
    require_comparable_latency: bool = True
    require_comparable_usage: bool = True
    require_comparable_cost: bool = True
    retain_on_optional_capability_loss: bool = True

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, bool)
            for value in (
                self.allow_compatible_unverified,
                self.require_comparable_latency,
                self.require_comparable_usage,
                self.require_comparable_cost,
                self.retain_on_optional_capability_loss,
            )
        ):
            raise ValueError("upgrade recommendation policy flags must be boolean")


@dataclass(frozen=True, slots=True)
class RecommendationDecisionV1:
    """Recommendation and deterministic reason codes, never action authority."""

    recommendation: UpgradeRecommendation
    reason_codes: tuple[str, ...]


def recommend_upgrade(
    comparison: UpgradeComparisonV1,
    *,
    policy: RecommendationPolicyV1 | None = None,
) -> RecommendationDecisionV1:
    """Derive one recommendation without updating, routing, or fallback effects."""
    if not isinstance(comparison, UpgradeComparisonV1):
        raise ValueError("upgrade recommendation comparison is invalid")
    active_policy = policy or RecommendationPolicyV1()
    compatibility = comparison.compatibility_delta
    candidate_status = compatibility.candidate_status
    if candidate_status in {
        CompatibilityStatus.UNSAFE,
        CompatibilityStatus.INCOMPATIBLE,
        CompatibilityStatus.UNAVAILABLE,
        CompatibilityStatus.DEGRADED,
    }:
        return _decision(
            UpgradeRecommendation.RETAIN_CURRENT,
            "candidate_structured_route_not_admitted",
        )
    if (
        not compatibility.protocol_family_match
        or not compatibility.protocol_version_match
    ):
        return _decision(
            UpgradeRecommendation.RETAIN_CURRENT,
            "candidate_protocol_changed",
        )
    capability = comparison.capability_delta
    if capability.new_required_losses:
        return _decision(
            UpgradeRecommendation.RETAIN_CURRENT,
            "candidate_missing_required_capability",
        )
    if active_policy.retain_on_optional_capability_loss and capability.removed:
        return _decision(
            UpgradeRecommendation.RETAIN_CURRENT,
            "candidate_removed_capability",
        )
    if any(
        item.candidate_status is OperationalEvidenceStatus.FAILED
        for item in comparison.gate_deltas
    ):
        return _decision(
            UpgradeRecommendation.RETAIN_CURRENT,
            "candidate_required_gate_failed",
        )
    if not comparison.candidate_required_gates_pass:
        return _decision(
            UpgradeRecommendation.INCOMPARABLE,
            "candidate_required_gate_unknown",
        )
    required_metrics = {
        "latency_total_ms": active_policy.require_comparable_latency,
        "usage_total_tokens": active_policy.require_comparable_usage,
        "known_cost_total_microunits": active_policy.require_comparable_cost,
    }
    metrics = {item.metric_id: item for item in comparison.metric_deltas}
    if any(
        required
        and (
            metric_id not in metrics
            or metrics[metric_id].direction is DeltaDirection.INCOMPARABLE
        )
        for metric_id, required in required_metrics.items()
    ):
        return _decision(
            UpgradeRecommendation.INCOMPARABLE,
            "candidate_required_metric_unknown",
        )
    if any(
        item.direction is DeltaDirection.REGRESSED for item in comparison.metric_deltas
    ):
        return _decision(
            UpgradeRecommendation.RETAIN_CURRENT,
            "candidate_metric_regressed",
        )
    if candidate_status is CompatibilityStatus.COMPATIBLE_UNVERIFIED and (
        not active_policy.allow_compatible_unverified
    ):
        return _decision(
            UpgradeRecommendation.NEEDS_HUMAN,
            "candidate_compatible_unverified",
        )
    return _decision(
        UpgradeRecommendation.PROMOTE_CANDIDATE,
        (
            "candidate_unverified_promotion_explicitly_allowed"
            if candidate_status is CompatibilityStatus.COMPATIBLE_UNVERIFIED
            else "candidate_verified_and_non_regressing"
        ),
    )


def build_upgrade_report(
    corpus: SealedCorpusV1,
    current: RouteEvaluationV1,
    candidate: RouteEvaluationV1,
    comparison: UpgradeComparisonV1,
    *,
    policy: RecommendationPolicyV1 | None = None,
) -> UpgradeRadarReportV1:
    """Project a comparison into the frozen recommendation-only report contract."""
    if corpus.sealed_digest != comparison.sealed_corpus_digest:
        raise ValueError("upgrade report corpus does not match comparison")
    if current.evaluation_digest != comparison.current_evaluation_digest or (
        candidate.evaluation_digest != comparison.candidate_evaluation_digest
    ):
        raise ValueError("upgrade report evaluations do not match comparison")
    decision = recommend_upgrade(comparison, policy=policy)
    report_seed = {
        "comparison_digest": comparison.comparison_digest,
        "recommendation": decision.recommendation.value,
        "reason_codes": list(decision.reason_codes),
    }
    report_digest = _digest(report_seed)
    return UpgradeRadarReportV1(
        report_id=f"upgrade-report-{report_digest[:24]}",
        sealed_corpus_digest=corpus.sealed_digest,
        current_route=current.route.route,
        candidate_route=candidate.route.route,
        compatibility_observations=(
            current.route.compatibility,
            candidate.route.compatibility,
        ),
        capability_delta=(_capability_evidence(comparison),),
        loss_delta=(_loss_evidence(comparison),),
        gate_results=tuple(
            [*_gate_evidence(comparison), _policy_evidence(decision, comparison)]
        ),
        latency_observations=_metric_evidence(comparison, "latency_total_ms"),
        usage_observations=_metric_evidence(comparison, "usage_total_tokens"),
        cost_observations=_metric_evidence(
            comparison,
            "known_cost_total_microunits",
        ),
        uncertainty=comparison.uncertainty,
        omissions=comparison.omissions,
        recommendation=decision.recommendation,
    )


def _capability_evidence(comparison: UpgradeComparisonV1) -> OperationalEvidenceV1:
    delta = comparison.capability_delta
    failed = bool(delta.new_required_losses)
    warning = bool(delta.removed)
    status = (
        OperationalEvidenceStatus.FAILED
        if failed
        else OperationalEvidenceStatus.WARNING
        if warning
        else OperationalEvidenceStatus.PASSED
    )
    return OperationalEvidenceV1(
        evidence_id="capability-delta",
        kind="upgrade_capability_delta",
        status=status,
        evidence_digest=_digest(
            {
                "added": list(delta.added),
                "removed": list(delta.removed),
                "new_required_losses": list(delta.new_required_losses),
                "resolved_required_losses": list(delta.resolved_required_losses),
            }
        ),
        reason_code=(
            "required_capability_lost"
            if failed
            else "optional_capability_removed"
            if warning
            else "no_capability_loss"
        ),
        source_digest=comparison.comparison_digest,
    )


def _loss_evidence(comparison: UpgradeComparisonV1) -> OperationalEvidenceV1:
    gate_regressions = sorted(
        f"{item.case_id}:{item.gate_id}"
        for item in comparison.gate_deltas
        if item.direction is DeltaDirection.REGRESSED
    )
    losses = [*comparison.capability_delta.new_required_losses, *gate_regressions]
    return OperationalEvidenceV1(
        evidence_id="loss-delta",
        kind="upgrade_loss_delta",
        status=(
            OperationalEvidenceStatus.FAILED
            if losses
            else OperationalEvidenceStatus.PASSED
        ),
        evidence_digest=_digest({"losses": losses}),
        reason_code="candidate_losses_detected" if losses else "no_candidate_losses",
        source_digest=comparison.comparison_digest,
    )


def _gate_evidence(
    comparison: UpgradeComparisonV1,
) -> list[OperationalEvidenceV1]:
    return [
        OperationalEvidenceV1(
            evidence_id=f"gate:{item.case_id}:{item.gate_id}",
            kind="upgrade_required_gate",
            status=item.candidate_status,
            evidence_digest=item.evidence_digest,
            reason_code=f"candidate_gate_{item.candidate_status.value}",
            source_digest=comparison.comparison_digest,
        )
        for item in comparison.gate_deltas
    ]


def _metric_evidence(
    comparison: UpgradeComparisonV1,
    metric_id: str,
) -> tuple[OperationalEvidenceV1, ...]:
    metric = next(
        item for item in comparison.metric_deltas if item.metric_id == metric_id
    )
    status = {
        DeltaDirection.IMPROVED: OperationalEvidenceStatus.PASSED,
        DeltaDirection.EQUIVALENT: OperationalEvidenceStatus.PASSED,
        DeltaDirection.REGRESSED: OperationalEvidenceStatus.WARNING,
        DeltaDirection.INCOMPARABLE: OperationalEvidenceStatus.UNKNOWN,
    }[metric.direction]
    return (
        OperationalEvidenceV1(
            evidence_id=f"metric:{metric.metric_id}",
            kind="upgrade_metric_delta",
            status=status,
            evidence_digest=metric.evidence_digest,
            reason_code=f"candidate_metric_{metric.direction.value}",
            source_digest=comparison.comparison_digest,
        ),
    )


def _policy_evidence(
    decision: RecommendationDecisionV1,
    comparison: UpgradeComparisonV1,
) -> OperationalEvidenceV1:
    status = {
        UpgradeRecommendation.PROMOTE_CANDIDATE: OperationalEvidenceStatus.PASSED,
        UpgradeRecommendation.RETAIN_CURRENT: OperationalEvidenceStatus.FAILED,
        UpgradeRecommendation.NEEDS_HUMAN: OperationalEvidenceStatus.WARNING,
        UpgradeRecommendation.INCOMPARABLE: OperationalEvidenceStatus.UNKNOWN,
    }[decision.recommendation]
    return OperationalEvidenceV1(
        evidence_id="recommendation-policy",
        kind="upgrade_recommendation_policy",
        status=status,
        evidence_digest=_digest(
            {
                "comparison_digest": comparison.comparison_digest,
                "recommendation": decision.recommendation.value,
                "reason_codes": list(decision.reason_codes),
            }
        ),
        reason_code=decision.reason_codes[0],
        source_digest=comparison.comparison_digest,
    )


def _decision(
    recommendation: UpgradeRecommendation,
    *reason_codes: str,
) -> RecommendationDecisionV1:
    return RecommendationDecisionV1(
        recommendation=recommendation,
        reason_codes=tuple(sorted(reason_codes)),
    )


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "RecommendationDecisionV1",
    "RecommendationPolicyV1",
    "build_upgrade_report",
    "recommend_upgrade",
]
