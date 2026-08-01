"""Recovery, upgrade, visual, and lane-delta contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from gigaloom.contracts import (
    AttachmentBindingStatus,
    CapabilityAdmissionV1,
    CompatibilityProbeCacheKeyV1,
    ExecutableObservationV1,
    LaneAttachmentBindingV1,
    LaneContentMode,
    LaneDeltaPacketV1,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTruncationV1,
    LaneTurnRangeV1,
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    RecoveryReceiptV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    RouteEvidenceV1,
    SecurityCompatibilityV1,
    UpgradeRadarReportV1,
    UpgradeRecommendation,
    VisualArtifactReferenceV1,
    VisualEvidenceSummaryV1,
    VisualGateReceiptV1,
    VisualGateStatus,
    VisualTolerancePolicyV1,
    VisualViewportV1,
    evaluate_compatibility,
    lane_delta_packet_from_dict,
    lane_delta_packet_to_dict,
    recovery_receipt_from_dict,
    recovery_receipt_to_dict,
    upgrade_radar_report_from_dict,
    upgrade_radar_report_to_dict,
    visual_gate_receipt_from_dict,
    visual_gate_receipt_to_dict,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _evidence(
    evidence_id: str,
    *,
    status: OperationalEvidenceStatus = OperationalEvidenceStatus.PASSED,
) -> OperationalEvidenceV1:
    return OperationalEvidenceV1(
        evidence_id=evidence_id,
        kind="contract_check",
        status=status,
        evidence_digest=_digest(evidence_id),
        reason_code="deterministic_result",
    )


def _compatibility(route_id: str, seed: str):
    executable_digest = _digest(f"executable:{seed}")
    profile_digest = _digest(f"profile:{seed}")
    handshake_digest = _digest(f"handshake:{seed}")
    capability_digest = _digest(f"capabilities:{seed}")
    cache_key = CompatibilityProbeCacheKeyV1(
        executable_identity=executable_digest,
        profile_digest=profile_digest,
        command_tokens_digest=_digest(f"command:{seed}"),
        protocol_handshake_digest=handshake_digest,
        platform="darwin-arm64",
    )
    return evaluate_compatibility(
        agent_id="fixture-agent",
        route_id=route_id,
        profile_digest=profile_digest,
        executable=ExecutableObservationV1(
            executable_identity=executable_digest,
            reported_version=None,
            observed=True,
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=ReviewedVersionState.UNKNOWN,
            evidence_digest=profile_digest,
            exact_evidence_matched=False,
        ),
        protocol=ProtocolNegotiationV1(
            protocol_family="acp",
            protocol_version="1",
            state=ProtocolNegotiationState.CONFORMANT,
            handshake_digest=handshake_digest,
        ),
        capabilities=CapabilityAdmissionV1(
            capability_fingerprint=capability_digest,
            required_capabilities=("session_new",),
            missing_capabilities=(),
        ),
        security=SecurityCompatibilityV1(),
        cache_key=cache_key,
        observed_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def test_recovery_receipt_is_content_free_bounded_and_round_trippable():
    receipt = RecoveryReceiptV1(
        receipt_id="recovery-1",
        data_root_fingerprint=_digest("data-root"),
        check_catalog_digest=_digest("catalog"),
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        checks=(_evidence("sqlite-integrity"),),
        derived_rebuilds=(_evidence("index-preview"),),
        quarantine_previews=(),
        fault_fixture_ids=("worker_dies_after_claim",),
        invariants=(_evidence("no-duplicate-side-effect"),),
        omissions=("raw_record_content",),
    )

    payload = recovery_receipt_to_dict(receipt)
    assert recovery_receipt_from_dict(payload) == receipt
    assert payload["content_free"] is True
    assert "data_root" not in payload
    with pytest.raises(ValueError, match="unknown fields"):
        recovery_receipt_from_dict({**payload, "raw_record": "forbidden"})


def _upgrade_report(
    *,
    failed_gate: bool = False,
    recommendation: UpgradeRecommendation = UpgradeRecommendation.NEEDS_HUMAN,
) -> UpgradeRadarReportV1:
    current = _compatibility("upgrade-route", "current")
    candidate = _compatibility("upgrade-route", "candidate")
    return UpgradeRadarReportV1(
        report_id="upgrade-report-1",
        sealed_corpus_digest=_digest("sealed-corpus"),
        current_route=RouteEvidenceV1(
            route_id=current.route_id,
            revision_digest=_digest("current-revision"),
            capability_fingerprint=current.capability_fingerprint,
            compatibility_observation_digest=current.probe_digest,
        ),
        candidate_route=RouteEvidenceV1(
            route_id=candidate.route_id,
            revision_digest=_digest("candidate-revision"),
            capability_fingerprint=candidate.capability_fingerprint,
            compatibility_observation_digest=candidate.probe_digest,
        ),
        compatibility_observations=(current, candidate),
        capability_delta=(_evidence("capability-delta"),),
        loss_delta=(_evidence("loss-delta"),),
        gate_results=(
            _evidence(
                "required-gates",
                status=(
                    OperationalEvidenceStatus.FAILED
                    if failed_gate
                    else OperationalEvidenceStatus.PASSED
                ),
            ),
        ),
        latency_observations=(_evidence("latency"),),
        usage_observations=(_evidence("usage"),),
        cost_observations=(_evidence("cost-unknown"),),
        uncertainty=("candidate_version_unreviewed",),
        omissions=("provider_raw_output",),
        recommendation=recommendation,
    )


def test_upgrade_report_round_trip_is_recommendation_only_and_digest_bound():
    report = _upgrade_report()
    payload = upgrade_radar_report_to_dict(report)

    assert upgrade_radar_report_from_dict(payload) == report
    assert payload["recommendation"] == "needs_human"
    assert len(payload["compatibility_observations"]) == 2

    with pytest.raises(ValueError, match="non-passing candidate gate"):
        _upgrade_report(
            failed_gate=True,
            recommendation=UpgradeRecommendation.PROMOTE_CANDIDATE,
        )


def _visual_receipt() -> VisualGateReceiptV1:
    viewports = (
        VisualViewportV1(viewport_id="desktop", width=1440, height=900),
        VisualViewportV1(viewport_id="mobile", width=390, height=844),
    )
    artifacts = tuple(
        VisualArtifactReferenceV1(
            artifact_id=f"screenshot-{viewport.viewport_id}",
            viewport_id=viewport.viewport_id,
            relative_path=f"screenshots/{viewport.viewport_id}.png",
            artifact_digest=_digest(f"screenshot:{viewport.viewport_id}"),
            media_type="image/png",
            byte_count=1024,
            redacted=True,
        )
        for viewport in viewports
    )
    return VisualGateReceiptV1(
        gate_id="visual-gate-1",
        origin="http://127.0.0.1:3000",
        origin_policy_digest=_digest("origin-policy"),
        source_revision="deadbeef",
        browser_fingerprint=_digest("browser"),
        viewports=viewports,
        assertions=(_evidence("no-overflow"),),
        console_summary=VisualEvidenceSummaryV1(
            total_count=1,
            failure_count=0,
            evidence_digest=_digest("console"),
        ),
        request_summary=VisualEvidenceSummaryV1(
            total_count=3,
            failure_count=0,
            evidence_digest=_digest("requests"),
        ),
        screenshot_artifacts=artifacts,
        redaction_receipt=_evidence("redaction"),
        tolerance_policy=VisualTolerancePolicyV1(
            policy_digest=_digest("tolerance"),
            max_console_errors=0,
            max_request_failures=0,
            max_overflow_pixels=0,
            max_timing_variance_ms=500,
        ),
        status=VisualGateStatus.PASSED,
    )


def test_visual_gate_requires_loopback_two_viewports_and_redacted_artifacts():
    receipt = _visual_receipt()
    payload = visual_gate_receipt_to_dict(receipt)

    assert visual_gate_receipt_from_dict(payload) == receipt
    assert {item["viewport_id"] for item in payload["viewports"]} == {
        "desktop",
        "mobile",
    }
    with pytest.raises(ValueError, match="loopback origin"):
        replace(receipt, origin="https://example.com")
    with pytest.raises(ValueError, match="must be redacted"):
        replace(receipt.screenshot_artifacts[0], redacted=False)
    with pytest.raises(ValueError, match="390x844"):
        replace(
            receipt,
            viewports=(
                receipt.viewports[0],
                replace(receipt.viewports[1], width=391),
            ),
        )
    with pytest.raises(ValueError, match="exceeds required evidence policy"):
        replace(
            receipt,
            redaction_receipt=replace(
                receipt.redaction_receipt,
                status=OperationalEvidenceStatus.FAILED,
            ),
        )


def _lane(agent_id: str, route_id: str) -> LaneIdentityV1:
    return LaneIdentityV1(
        agent_id=agent_id,
        route_id=route_id,
        model_id="model-1",
        account_ref="account-1",
        workspace_fingerprint=_digest("workspace"),
        policy_digest=_digest("policy"),
        context_manifest_digest=_digest(f"context:{agent_id}:{route_id}"),
        run_capsule_digest=_digest(f"capsule:{agent_id}:{route_id}"),
        session_id="session-1",
    )


def test_lane_delta_round_trip_binds_attachments_and_rejects_unchanged_lane():
    source = _lane("codex", "native")
    destination = _lane("example-acp", "acp-v1")
    packet = LaneDeltaPacketV1(
        packet_id="lane-packet-1",
        source_lane=source,
        destination_lane=destination,
        last_shared_turn="turn-7",
        missed_turn_range=LaneTurnRangeV1(first_sequence=8, last_sequence=10),
        context_manifest_digests=(
            source.context_manifest_digest,
            destination.context_manifest_digest,
        ),
        run_capsule_digests=(
            source.run_capsule_digest,
            destination.run_capsule_digest,
        ),
        changed_anchors=(_evidence("changed-file-anchor"),),
        attachment_bindings=(
            LaneAttachmentBindingV1(
                attachment_id="attachment-1",
                expected_digest=_digest("attachment"),
                observed_digest=_digest("attachment"),
                status=AttachmentBindingStatus.AVAILABLE,
            ),
        ),
        disclosure_mode=LaneDisclosureMode.PACKET,
        truncation=LaneTruncationV1(
            truncated=False,
            original_count=1,
            included_count=1,
            reason_code=None,
        ),
        omissions=("full_session_history",),
        created_at=NOW,
    )
    payload = lane_delta_packet_to_dict(packet)

    assert lane_delta_packet_from_dict(payload) == packet
    assert payload["disclosure_mode"] == "packet"
    assert payload["content_mode"] == LaneContentMode.CONTENT_FREE.value
    assert "content" not in payload

    with pytest.raises(ValueError, match="unchanged lane"):
        replace(packet, destination_lane=source)
    with pytest.raises(ValueError, match="matching digest"):
        LaneAttachmentBindingV1(
            attachment_id="attachment-invalid",
            expected_digest=_digest("expected"),
            observed_digest=_digest("observed"),
            status=AttachmentBindingStatus.AVAILABLE,
        )
