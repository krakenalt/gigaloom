"""Protocol-first compatibility observation contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from gigaloom.contracts import (
    CapabilityAdmissionV1,
    CompatibilityConfidence,
    CompatibilityObservationCache,
    CompatibilityProbeCacheKeyV1,
    CompatibilityStatus,
    ExecutableObservationV1,
    KnownIncompatibilityV1,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    SecurityCompatibilityV1,
    compatibility_observation_to_dict,
    evaluate_compatibility,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
PROFILE_DIGEST = hashlib.sha256(b"profile").hexdigest()
EXECUTABLE_DIGEST = hashlib.sha256(b"executable").hexdigest()
COMMAND_DIGEST = hashlib.sha256(b"command").hexdigest()
HANDSHAKE_DIGEST = hashlib.sha256(b"handshake").hexdigest()
CAPABILITY_DIGEST = hashlib.sha256(b"capabilities").hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _key(**overrides: str) -> CompatibilityProbeCacheKeyV1:
    values = {
        "executable_identity": EXECUTABLE_DIGEST,
        "profile_digest": PROFILE_DIGEST,
        "command_tokens_digest": COMMAND_DIGEST,
        "protocol_handshake_digest": HANDSHAKE_DIGEST,
        "platform": "darwin-arm64",
        **overrides,
    }
    return CompatibilityProbeCacheKeyV1(**values)


def _observation(
    *,
    reviewed_state: ReviewedVersionState = ReviewedVersionState.UNKNOWN,
    exact_evidence_matched: bool = False,
    protocol_state: ProtocolNegotiationState = ProtocolNegotiationState.CONFORMANT,
    protocol_version: str | None = "1",
    missing_capabilities: tuple[str, ...] = (),
    invariant_failures: tuple[str, ...] = (),
    known_incompatibilities: tuple[KnownIncompatibilityV1, ...] = (),
    cache_key: CompatibilityProbeCacheKeyV1 | None = None,
):
    key = cache_key or _key()
    return evaluate_compatibility(
        agent_id="acp_agent",
        route_id="acp_v1",
        profile_digest=PROFILE_DIGEST,
        executable=ExecutableObservationV1(
            executable_identity=EXECUTABLE_DIGEST,
            reported_version=None,
            observed=True,
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=reviewed_state,
            evidence_digest=PROFILE_DIGEST,
            exact_evidence_matched=exact_evidence_matched,
        ),
        protocol=ProtocolNegotiationV1(
            protocol_family="acp",
            protocol_version=protocol_version,
            state=protocol_state,
            handshake_digest=HANDSHAKE_DIGEST,
        ),
        capabilities=CapabilityAdmissionV1(
            capability_fingerprint=CAPABILITY_DIGEST,
            required_capabilities=("session_load", "session_new"),
            missing_capabilities=missing_capabilities,
        ),
        security=SecurityCompatibilityV1(
            invariant_failures=invariant_failures,
            known_incompatibilities=known_incompatibilities,
        ),
        cache_key=key,
        observed_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def test_unknown_version_with_successful_acp_v1_is_compatible_unverified():
    observation = _observation()

    assert observation.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED
    assert observation.confidence is CompatibilityConfidence.OBSERVED
    assert observation.native_eligible is True
    assert observation.structured_admitted(allow_unverified=True) is True
    assert observation.structured_admitted(allow_unverified=False) is False


def test_reviewed_version_cannot_override_missing_mandatory_method():
    observation = _observation(
        reviewed_state=ReviewedVersionState.IN_RANGE,
        exact_evidence_matched=True,
        missing_capabilities=("session_load",),
    )

    assert observation.status is CompatibilityStatus.DEGRADED
    assert observation.missing_capabilities == ("session_load",)
    assert observation.structured_admitted(allow_unverified=True) is False


@pytest.mark.parametrize(
    ("protocol_state", "protocol_version", "invariant_failures", "status"),
    (
        (
            ProtocolNegotiationState.MAJOR_MISMATCH,
            "2",
            (),
            CompatibilityStatus.INCOMPATIBLE,
        ),
        (
            ProtocolNegotiationState.MALFORMED,
            None,
            ("malformed_jsonrpc_framing",),
            CompatibilityStatus.UNSAFE,
        ),
    ),
)
def test_protocol_invariant_failures_have_explicit_terminal_status(
    protocol_state,
    protocol_version,
    invariant_failures,
    status,
):
    observation = _observation(
        protocol_state=protocol_state,
        protocol_version=protocol_version,
        invariant_failures=invariant_failures,
    )

    assert observation.status is status
    assert observation.structured_admitted(allow_unverified=True) is False


def test_outside_reviewed_window_is_only_a_hint_when_conformance_passes():
    observation = _observation(reviewed_state=ReviewedVersionState.OUTSIDE_RANGE)

    assert observation.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED
    assert "version_outside_range" in observation.reason_codes


def test_known_bad_rule_requires_explicit_digest_bound_evidence():
    known_bad = KnownIncompatibilityV1(
        rule_id="acp_v1_frame_reuse",
        reason_code="unsafe_frame_reuse",
        evidence_digest=_digest("reviewed known-bad evidence"),
    )

    observation = _observation(known_incompatibilities=(known_bad,))

    assert observation.status is CompatibilityStatus.INCOMPATIBLE
    assert observation.known_incompatibilities == (known_bad,)
    assert "known_bad:acp_v1_frame_reuse" in observation.reason_codes


def test_observation_serialization_is_content_free_and_digest_bound():
    observation = _observation()
    payload = compatibility_observation_to_dict(observation)

    assert payload["observation_id"].startswith("compat-")
    assert payload["probe_digest"] == observation.probe_digest
    assert payload["cache_key_digest"] == _key().digest
    assert payload["protocol_family"] == "acp"
    assert payload["protocol_version"] == "1"
    assert payload["content_free"] is True
    assert payload["observed_at"] == NOW.isoformat()
    assert "command" not in payload


def test_cache_invalidates_on_every_required_fingerprint_and_expiry():
    base_key = _key()
    observation = _observation(cache_key=base_key)
    cache = CompatibilityObservationCache(max_entries=2)
    cache.put(base_key, observation)

    assert cache.get(base_key, now=NOW) is observation
    for key in (
        _key(executable_identity=_digest("other executable")),
        _key(profile_digest=_digest("other profile")),
        _key(command_tokens_digest=_digest("other command")),
        _key(protocol_handshake_digest=_digest("other handshake")),
        _key(platform="linux-x86_64"),
    ):
        assert cache.get(key, now=NOW) is None
    assert cache.get(base_key, now=observation.expires_at) is None


def test_cache_rejects_observation_bound_to_another_key():
    cache = CompatibilityObservationCache()

    with pytest.raises(ValueError, match="cache key mismatch"):
        cache.put(
            _key(executable_identity=_digest("other executable")),
            _observation(),
        )

    with pytest.raises(ValueError, match="cache key mismatch"):
        cache.put(_key(), replace(_observation(), cache_key_digest=_digest("wrong")))
