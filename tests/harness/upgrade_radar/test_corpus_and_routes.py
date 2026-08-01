"""Sealed corpus and immutable route-provenance tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from gigaloom.contracts import (
    CapabilityAdmissionV1,
    CompatibilityProbeCacheKeyV1,
    ExecutableObservationV1,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    RouteEvidenceV1,
    SecurityCompatibilityV1,
    evaluate_compatibility,
)
from gigaloom.diagnostics.upgrade_radar import load_sealed_corpus
from gigaloom.diagnostics.upgrade_radar.contracts import (
    RouteSnapshotV1,
    route_snapshot_from_dict,
    route_snapshot_to_dict,
    sealed_corpus_from_dict,
    sealed_corpus_to_dict,
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


def _route_snapshot(seed: str = "current") -> RouteSnapshotV1:
    profile_digest = _digest(f"profile:{seed}")
    command_digest = _digest(f"command:{seed}")
    handshake_digest = _digest(f"handshake:{seed}")
    capability_digest = _digest(f"capabilities:{seed}")
    executable_digest = _digest(f"executable:{seed}")
    observation = evaluate_compatibility(
        agent_id="fixture-agent",
        route_id="fixture-route",
        profile_digest=profile_digest,
        executable=ExecutableObservationV1(
            executable_identity=executable_digest,
            reported_version="1.0.0",
            observed=True,
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=ReviewedVersionState.IN_RANGE,
            evidence_digest=_digest(f"reviewed:{seed}"),
            exact_evidence_matched=True,
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
            missing_capabilities=(),
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


def test_sealed_corpus_is_content_free_exact_and_byte_stable() -> None:
    corpus = load_sealed_corpus(FIXTURE)

    assert corpus.corpus_id == "sealed-smoke"
    assert corpus.sealed_digest == (
        "fbd4cb9eb79d6a3a332b2f9951dfa23c97ca4b51a81472db324f06b3cc935b50"
    )
    assert [case.case_id for case in corpus.cases] == [
        "structured-output",
        "tool-call",
    ]
    assert sealed_corpus_from_dict(sealed_corpus_to_dict(corpus)) == corpus
    payload = sealed_corpus_to_dict(corpus)
    forbidden_content_fields = {"prompt", "response", "content", "secret"}
    assert forbidden_content_fields.isdisjoint(payload)
    for case in payload["cases"]:
        assert forbidden_content_fields.isdisjoint(case)


def test_sealed_corpus_rejects_tampering_unknown_fields_and_symlinks(
    tmp_path: Path,
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["cases"][0]["request_digest"] = _digest("tampered")
    with pytest.raises(ValueError, match="digest does not match"):
        sealed_corpus_from_dict(payload)

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["raw_prompt"] = "must not be admitted"
    with pytest.raises(ValueError, match="unknown or missing fields"):
        sealed_corpus_from_dict(payload)

    link = tmp_path / "corpus.json"
    link.symlink_to(FIXTURE)
    with pytest.raises(ValueError, match="regular file"):
        load_sealed_corpus(link)


def test_route_snapshot_binds_all_provenance_and_round_trips() -> None:
    snapshot = _route_snapshot()
    payload = route_snapshot_to_dict(snapshot)

    assert route_snapshot_from_dict(payload) == snapshot
    assert payload["snapshot_digest"] == snapshot.snapshot_digest
    assert payload["compatibility"]["agent_id"] == "fixture-agent"
    assert payload["compatibility"]["executable_identity"] == _digest(
        "executable:current"
    )
    assert payload["compatibility"]["protocol_version"] == "1"
    assert payload["route"]["capability_fingerprint"] == _digest("capabilities:current")
    assert "command" not in payload

    with pytest.raises(FrozenInstanceError):
        snapshot.model_identity = "mutated"  # type: ignore[misc]


def test_route_snapshot_rejects_cross_revision_evidence_and_digest_drift() -> None:
    current = _route_snapshot("current")
    candidate = _route_snapshot("candidate")

    with pytest.raises(ValueError, match="does not match compatibility"):
        replace(current, compatibility=candidate.compatibility)

    payload = route_snapshot_to_dict(current)
    payload["model_identity"] = "different-model"
    with pytest.raises(ValueError, match="digest does not match"):
        route_snapshot_from_dict(payload)
