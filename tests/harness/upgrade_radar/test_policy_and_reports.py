"""Recommendation-only policy and immutable report replay tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import stat

import pytest

from gigaloom.contracts import (
    CapabilityAdmissionV1,
    CompatibilityProbeCacheKeyV1,
    ExecutableObservationV1,
    OperationalEvidenceStatus,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    RouteEvidenceV1,
    SecurityCompatibilityV1,
    UpgradeRecommendation,
    evaluate_compatibility,
)
from gigaloom.diagnostics.upgrade_radar import (
    CaseObservationV1,
    GateObservationV1,
    RecommendationPolicyV1,
    RouteEvaluationV1,
    RouteSnapshotV1,
    build_upgrade_report,
    compare_route_evaluations,
    load_sealed_corpus,
    load_upgrade_report,
    recommend_upgrade,
    save_upgrade_report,
    upgrade_report_bytes,
    upgrade_report_from_bytes,
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
    profile_digest = _digest(f"policy-profile:{seed}")
    command_digest = _digest(f"policy-command:{seed}")
    executable_digest = _digest(f"policy-executable:{seed}")
    handshake_digest = _digest(f"policy-handshake:{seed}")
    capability_digest = _digest(f"policy-capabilities:{seed}")
    observation = evaluate_compatibility(
        agent_id="policy-fixture-agent",
        route_id="policy-fixture-route",
        profile_digest=profile_digest,
        executable=ExecutableObservationV1(
            executable_identity=executable_digest,
            reported_version="2.0.0" if seed == "candidate" else "1.0.0",
            observed=True,
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=reviewed_state,
            evidence_digest=_digest(f"policy-reviewed:{seed}"),
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
            revision_digest=_digest(f"policy-revision:{seed}"),
            capability_fingerprint=observation.capability_fingerprint,
            compatibility_observation_digest=observation.probe_digest,
        ),
        compatibility=observation,
        command_tokens_digest=command_digest,
        model_identity=f"policy-model-{seed}",
    )


def _gate(
    seed: str,
    gate_id: str,
    status: OperationalEvidenceStatus = OperationalEvidenceStatus.PASSED,
) -> GateObservationV1:
    return GateObservationV1(
        gate_id=gate_id,
        status=status,
        evidence_digest=_digest(f"policy:{seed}:{gate_id}:{status.value}"),
    )


def _evaluation(
    seed: str,
    *,
    candidate: bool,
    missing_capabilities: tuple[str, ...] = (),
    reviewed_state: ReviewedVersionState = ReviewedVersionState.IN_RANGE,
    fail_tool_gate: bool = False,
    cost_known: bool = True,
) -> RouteEvaluationV1:
    corpus = load_sealed_corpus(FIXTURE)
    latency_factor = 1 if candidate else 2
    tool_status = (
        OperationalEvidenceStatus.FAILED
        if fail_tool_gate
        else OperationalEvidenceStatus.PASSED
    )
    return RouteEvaluationV1(
        route=_snapshot(
            seed,
            missing_capabilities=missing_capabilities,
            reviewed_state=reviewed_state,
        ),
        sealed_corpus_digest=corpus.sealed_digest,
        supported_capabilities=(
            ("structured_output",)
            if missing_capabilities
            else ("structured_output", "tool_call")
        ),
        cases=(
            CaseObservationV1(
                case_id="structured-output",
                gates=(
                    _gate(seed, "response_valid"),
                    _gate(seed, "structured_output_valid"),
                ),
                latency_ms=50 * latency_factor,
                input_tokens=10,
                output_tokens=10,
                known_cost_microunits=20,
            ),
            CaseObservationV1(
                case_id="tool-call",
                gates=(
                    _gate(seed, "response_valid"),
                    _gate(seed, "tool_behavior_valid", tool_status),
                ),
                latency_ms=100 * latency_factor,
                input_tokens=10,
                output_tokens=20 if not candidate else 10,
                known_cost_microunits=(20 if cost_known else None),
                omissions=("provider_raw_output",),
            ),
        ),
    )


def _artifacts(**candidate_options):
    corpus = load_sealed_corpus(FIXTURE)
    current = _evaluation("current", candidate=False)
    candidate = _evaluation("candidate", candidate=True, **candidate_options)
    comparison = compare_route_evaluations(corpus, current, candidate)
    return corpus, current, candidate, comparison


def test_faster_candidate_missing_required_capability_retains_current() -> None:
    corpus, current, candidate, comparison = _artifacts(
        missing_capabilities=("tool_call",),
        fail_tool_gate=True,
    )

    decision = recommend_upgrade(comparison)
    report = build_upgrade_report(corpus, current, candidate, comparison)

    assert decision.recommendation is UpgradeRecommendation.RETAIN_CURRENT
    assert report.recommendation is UpgradeRecommendation.RETAIN_CURRENT
    assert report.latency_observations[0].status is OperationalEvidenceStatus.PASSED
    assert report.capability_delta[0].status is OperationalEvidenceStatus.FAILED
    assert any(
        item.reason_code == "candidate_structured_route_not_admitted"
        for item in report.gate_results
    )
    assert b"fallback" not in upgrade_report_bytes(report)


def test_unreviewed_version_needs_human_or_promotes_only_by_explicit_policy() -> None:
    corpus, current, candidate, comparison = _artifacts(
        reviewed_state=ReviewedVersionState.OUTSIDE_RANGE,
    )

    default = recommend_upgrade(comparison)
    explicit = recommend_upgrade(
        comparison,
        policy=RecommendationPolicyV1(allow_compatible_unverified=True),
    )
    explicit_report = build_upgrade_report(
        corpus,
        current,
        candidate,
        comparison,
        policy=RecommendationPolicyV1(allow_compatible_unverified=True),
    )

    assert default.recommendation is UpgradeRecommendation.NEEDS_HUMAN
    assert explicit.recommendation is UpgradeRecommendation.PROMOTE_CANDIDATE
    assert explicit_report.recommendation is UpgradeRecommendation.PROMOTE_CANDIDATE


def test_unknown_cost_remains_unknown_and_makes_default_report_incomparable() -> None:
    corpus, current, candidate, comparison = _artifacts(cost_known=False)

    report = build_upgrade_report(corpus, current, candidate, comparison)

    assert report.recommendation is UpgradeRecommendation.INCOMPARABLE
    assert report.cost_observations[0].status is OperationalEvidenceStatus.UNKNOWN
    assert "known_cost_total_microunits_unknown" in report.uncertainty


def test_failed_candidate_never_becomes_fallback_or_mutation_authority() -> None:
    corpus, current, candidate, comparison = _artifacts(fail_tool_gate=True)

    report = build_upgrade_report(corpus, current, candidate, comparison)
    payload = upgrade_report_bytes(report)

    assert report.recommendation is UpgradeRecommendation.RETAIN_CURRENT
    assert b"fallback" not in payload
    assert b"automatic_apply" not in payload
    assert b"update_authorized" not in payload


def test_report_replay_is_byte_stable_private_and_immutable(tmp_path: Path) -> None:
    corpus, current, candidate, comparison = _artifacts()
    report = build_upgrade_report(corpus, current, candidate, comparison)
    payload = upgrade_report_bytes(report)
    target = tmp_path / "reports" / f"{report.report_id}.json"

    assert report.recommendation is UpgradeRecommendation.PROMOTE_CANDIDATE
    assert b"\x1b" not in payload
    assert upgrade_report_from_bytes(payload) == report
    save_upgrade_report(target, report)
    save_upgrade_report(target, report)
    assert load_upgrade_report(target) == report
    assert target.read_bytes() == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    with pytest.raises(ValueError, match="already exists"):
        save_upgrade_report(target, replace(report, report_id="different-report"))
    with pytest.raises(ValueError, match="not canonical"):
        upgrade_report_from_bytes(b" " + payload)

    link = tmp_path / "report-link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        load_upgrade_report(link)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="bounded policy"):
        load_upgrade_report(oversized)
