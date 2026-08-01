"""Deterministic current-versus-candidate comparison tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from gigaloom.contracts import (
    CapabilityAdmissionV1,
    CompatibilityProbeCacheKeyV1,
    CompatibilityStatus,
    ExecutableObservationV1,
    OperationalEvidenceStatus,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    RouteEvidenceV1,
    SecurityCompatibilityV1,
    evaluate_compatibility,
)
from gigaloom.diagnostics.upgrade_radar import (
    CaseObservationV1,
    ComparisonPolicyV1,
    DeltaDirection,
    GateObservationV1,
    RouteEvaluationV1,
    RouteSnapshotV1,
    compare_route_evaluations,
    load_sealed_corpus,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "upgrade_radar"
    / "sealed-smoke.json"
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _snapshot(
    seed: str,
    *,
    missing_capabilities: tuple[str, ...] = (),
    reviewed_state: ReviewedVersionState = ReviewedVersionState.IN_RANGE,
) -> RouteSnapshotV1:
    profile_digest = _digest(f"profile:{seed}")
    command_digest = _digest(f"command:{seed}")
    executable_digest = _digest(f"executable:{seed}")
    handshake_digest = _digest(f"handshake:{seed}")
    capability_digest = _digest(f"capabilities:{seed}")
    observation = evaluate_compatibility(
        agent_id="fixture-agent",
        route_id="fixture-route",
        profile_digest=profile_digest,
        executable=ExecutableObservationV1(
            executable_identity=executable_digest,
            reported_version="2.0.0" if seed == "candidate" else "1.0.0",
            observed=True,
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=reviewed_state,
            evidence_digest=_digest(f"reviewed:{seed}"),
            exact_evidence_matched=reviewed_state is ReviewedVersionState.IN_RANGE,
        ),
        protocol=ProtocolNegotiationV1(
            protocol_family="acp",
            protocol_version="1",
            state=ProtocolNegotiationState.CONFORMANT,
            handshake_digest=handshake_digest,
        ),
        capabilities=CapabilityAdmissionV1(
            capability_fingerprint=capability_digest,
            required_capabilities=("structured_output", "tool_call"),
            missing_capabilities=missing_capabilities,
        ),
        security=SecurityCompatibilityV1(),
        cache_key=CompatibilityProbeCacheKeyV1(
            executable_identity=executable_digest,
            profile_digest=profile_digest,
            command_tokens_digest=command_digest,
            protocol_handshake_digest=handshake_digest,
            platform="linux-x86_64",
        ),
        observed_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    return RouteSnapshotV1(
        route=RouteEvidenceV1(
            route_id=observation.route_id,
            revision_digest=_digest(f"revision:{seed}"),
            capability_fingerprint=observation.capability_fingerprint,
            compatibility_observation_digest=observation.probe_digest,
        ),
        compatibility=observation,
        command_tokens_digest=command_digest,
        model_identity=f"fixture-model-{seed}",
    )


def _gate(
    seed: str,
    gate_id: str,
    status: OperationalEvidenceStatus = OperationalEvidenceStatus.PASSED,
) -> GateObservationV1:
    return GateObservationV1(
        gate_id=gate_id,
        status=status,
        evidence_digest=_digest(f"{seed}:{gate_id}:{status.value}"),
    )


def _evaluation(
    seed: str,
    *,
    candidate: bool = False,
    missing_capabilities: tuple[str, ...] = (),
    reviewed_state: ReviewedVersionState = ReviewedVersionState.IN_RANGE,
) -> RouteEvaluationV1:
    corpus = load_sealed_corpus(FIXTURE)
    multiplier = 1 if candidate else 2
    cases = (
        CaseObservationV1(
            case_id="tool-call",
            gates=(
                _gate(seed, "response_valid"),
                _gate(
                    seed,
                    "tool_behavior_valid",
                    (
                        OperationalEvidenceStatus.FAILED
                        if missing_capabilities
                        else OperationalEvidenceStatus.PASSED
                    ),
                ),
            ),
            latency_ms=100 * multiplier,
            input_tokens=10,
            output_tokens=20,
            known_cost_microunits=None if candidate else 30,
            omissions=("provider_raw_output",),
        ),
        CaseObservationV1(
            case_id="structured-output",
            gates=(
                _gate(seed, "response_valid"),
                _gate(seed, "structured_output_valid"),
            ),
            latency_ms=50 * multiplier,
            input_tokens=10,
            output_tokens=10 if candidate else 20,
            known_cost_microunits=20,
        ),
    )
    supported = (
        ("structured_output",)
        if missing_capabilities
        else ("structured_output", "tool_call")
    )
    return RouteEvaluationV1(
        route=_snapshot(
            seed,
            missing_capabilities=missing_capabilities,
            reviewed_state=reviewed_state,
        ),
        sealed_corpus_digest=corpus.sealed_digest,
        supported_capabilities=supported,
        cases=cases,
    )


def test_comparison_covers_compatibility_capability_gates_and_metrics() -> None:
    corpus = load_sealed_corpus(FIXTURE)
    current = _evaluation("current")
    candidate = _evaluation(
        "candidate",
        candidate=True,
        missing_capabilities=("tool_call",),
        reviewed_state=ReviewedVersionState.OUTSIDE_RANGE,
    )

    comparison = compare_route_evaluations(corpus, current, candidate)

    assert comparison.compatibility_delta.current_status is CompatibilityStatus.VERIFIED
    assert (
        comparison.compatibility_delta.candidate_status is CompatibilityStatus.DEGRADED
    )
    assert comparison.capability_delta.removed == ("tool_call",)
    assert comparison.capability_delta.new_required_losses == ("tool_call",)
    tool_delta = next(
        item for item in comparison.gate_deltas if item.gate_id == "tool_behavior_valid"
    )
    assert tool_delta.direction is DeltaDirection.REGRESSED
    assert tool_delta.candidate_status is OperationalEvidenceStatus.FAILED

    metrics = {item.metric_id: item for item in comparison.metric_deltas}
    assert metrics["latency_total_ms"].current_value == 300
    assert metrics["latency_total_ms"].candidate_value == 150
    assert metrics["latency_total_ms"].delta_basis_points == -5000
    assert metrics["latency_total_ms"].direction is DeltaDirection.IMPROVED
    assert metrics["usage_total_tokens"].direction is DeltaDirection.IMPROVED
    assert (
        metrics["known_cost_total_microunits"].direction is DeltaDirection.INCOMPARABLE
    )
    assert "known_cost_total_microunits_unknown" in comparison.uncertainty
    assert comparison.candidate_required_gates_pass is False
    assert comparison.candidate_has_regression is True


def test_comparison_is_order_independent_and_byte_stable() -> None:
    corpus = load_sealed_corpus(FIXTURE)
    current = _evaluation("current")
    candidate = _evaluation("candidate", candidate=True)
    reordered = replace(
        candidate,
        supported_capabilities=tuple(reversed(candidate.supported_capabilities)),
        cases=tuple(reversed(candidate.cases)),
    )

    first = compare_route_evaluations(
        corpus,
        current,
        candidate,
        policy=ComparisonPolicyV1(max_concurrency=2),
    )
    second = compare_route_evaluations(
        corpus,
        current,
        reordered,
        policy=ComparisonPolicyV1(max_concurrency=2),
    )

    assert candidate.evaluation_digest == reordered.evaluation_digest
    assert first == second
    assert first.comparison_digest == second.comparison_digest


def test_comparison_rejects_incomplete_corpus_or_gate_coverage() -> None:
    corpus = load_sealed_corpus(FIXTURE)
    current = _evaluation("current")
    candidate = _evaluation("candidate", candidate=True)

    with pytest.raises(ValueError, match="case coverage is incomplete"):
        compare_route_evaluations(
            corpus,
            replace(current, cases=current.cases[:1]),
            candidate,
        )

    broken_case = replace(candidate.cases[0], gates=candidate.cases[0].gates[:1])
    with pytest.raises(ValueError, match="gate coverage is incomplete"):
        compare_route_evaluations(
            corpus,
            current,
            replace(candidate, cases=(broken_case, *candidate.cases[1:])),
        )


def test_unknown_gate_and_zero_baseline_are_never_fabricated() -> None:
    corpus = load_sealed_corpus(FIXTURE)
    current = _evaluation("current")
    candidate = _evaluation("candidate", candidate=True)
    unknown_gate = replace(
        candidate.cases[0].gates[0],
        status=OperationalEvidenceStatus.UNKNOWN,
    )
    unknown_case = replace(
        candidate.cases[0],
        gates=(unknown_gate, *candidate.cases[0].gates[1:]),
    )

    comparison = compare_route_evaluations(
        corpus,
        current,
        replace(candidate, cases=(unknown_case, *candidate.cases[1:])),
    )

    assert any(
        item.direction is DeltaDirection.INCOMPARABLE for item in comparison.gate_deltas
    )
    assert any(item.startswith("gate_unknown:") for item in comparison.uncertainty)
    with pytest.raises(ValueError, match="concurrency is out of bounds"):
        ComparisonPolicyV1(max_concurrency=9)
