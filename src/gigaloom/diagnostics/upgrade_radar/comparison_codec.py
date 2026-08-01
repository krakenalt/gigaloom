"""Canonical internal serialization for upgrade comparison evidence."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from gigaloom.diagnostics.upgrade_radar.comparison import (
        RouteEvaluationV1,
        UpgradeComparisonV1,
    )


def evaluation_body(evaluation: RouteEvaluationV1) -> dict[str, Any]:
    """Return the identity-free canonical evaluation body."""
    return {
        "route_snapshot_digest": evaluation.route.snapshot_digest,
        "sealed_corpus_digest": evaluation.sealed_corpus_digest,
        "supported_capabilities": list(evaluation.supported_capabilities),
        "cases": [
            {
                "case_id": case.case_id,
                "gates": [
                    {
                        "gate_id": gate.gate_id,
                        "status": gate.status.value,
                        "evidence_digest": gate.evidence_digest,
                    }
                    for gate in case.gates
                ],
                "latency_ms": case.latency_ms,
                "input_tokens": case.input_tokens,
                "output_tokens": case.output_tokens,
                "known_cost_microunits": case.known_cost_microunits,
                "omissions": list(case.omissions),
            }
            for case in evaluation.cases
        ],
    }


def comparison_body(comparison: UpgradeComparisonV1) -> dict[str, Any]:
    """Return the identity-free canonical comparison body."""
    capability = comparison.capability_delta
    compatibility = comparison.compatibility_delta
    return {
        "sealed_corpus_digest": comparison.sealed_corpus_digest,
        "current_evaluation_digest": comparison.current_evaluation_digest,
        "candidate_evaluation_digest": comparison.candidate_evaluation_digest,
        "policy": {
            "max_concurrency": comparison.policy.max_concurrency,
            "metric_equivalence_basis_points": (
                comparison.policy.metric_equivalence_basis_points
            ),
        },
        "compatibility_delta": {
            "current_status": compatibility.current_status.value,
            "candidate_status": compatibility.candidate_status.value,
            "current_confidence": compatibility.current_confidence.value,
            "candidate_confidence": compatibility.candidate_confidence.value,
            "current_reviewed_version": compatibility.current_reviewed_version.value,
            "candidate_reviewed_version": (
                compatibility.candidate_reviewed_version.value
            ),
            "protocol_family_match": compatibility.protocol_family_match,
            "protocol_version_match": compatibility.protocol_version_match,
        },
        "capability_delta": {
            "added": list(capability.added),
            "removed": list(capability.removed),
            "new_required_losses": list(capability.new_required_losses),
            "resolved_required_losses": list(capability.resolved_required_losses),
        },
        "gate_deltas": [
            {
                "case_id": item.case_id,
                "gate_id": item.gate_id,
                "current_status": item.current_status.value,
                "candidate_status": item.candidate_status.value,
                "direction": item.direction.value,
                "evidence_digest": item.evidence_digest,
            }
            for item in comparison.gate_deltas
        ],
        "metric_deltas": [
            {
                "metric_id": item.metric_id,
                "current_value": item.current_value,
                "candidate_value": item.candidate_value,
                "delta_basis_points": item.delta_basis_points,
                "direction": item.direction.value,
                "evidence_digest": item.evidence_digest,
            }
            for item in comparison.metric_deltas
        ],
        "uncertainty": list(comparison.uncertainty),
        "omissions": list(comparison.omissions),
    }


def canonical_digest(payload: Mapping[str, Any]) -> str:
    """Hash canonical ASCII JSON for replay-stable identity."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["canonical_digest", "comparison_body", "evaluation_body"]
