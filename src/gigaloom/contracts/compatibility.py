"""Protocol-first, content-free compatibility observation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import re
from typing import Any

from gigaloom.contracts.compatibility_fingerprints import (
    CompatibilityProbeCacheKeyV1,
)


COMPATIBILITY_OBSERVATION_SCHEMA_VERSION = 1
MAX_COMPATIBILITY_CAPABILITIES = 128
MAX_COMPATIBILITY_REASONS = 32

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}\Z")


class CompatibilityStatus(str, Enum):
    """Stable route-compatibility outcome."""

    VERIFIED = "verified"
    COMPATIBLE_UNVERIFIED = "compatible_unverified"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    UNSAFE = "unsafe"


class CompatibilityConfidence(str, Enum):
    """Strength of the evidence supporting an observation."""

    REVIEWED = "reviewed"
    OBSERVED = "observed"
    LIMITED = "limited"
    NONE = "none"


class ReviewedVersionState(str, Enum):
    """Relationship between a reported version and reviewed evidence."""

    IN_RANGE = "in_range"
    OUTSIDE_RANGE = "outside_range"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ProtocolNegotiationState(str, Enum):
    """Result of a non-mutating structured-protocol negotiation."""

    CONFORMANT = "conformant"
    UNAVAILABLE = "unavailable"
    MAJOR_MISMATCH = "major_mismatch"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class ReviewedVersionEvidenceV1:
    """Static version review evidence, separate from live conformance."""

    state: ReviewedVersionState
    evidence_digest: str
    exact_evidence_matched: bool

    def __post_init__(self) -> None:
        if not isinstance(self.state, ReviewedVersionState):
            raise ValueError("reviewed version state is invalid")
        _validate_digest(self.evidence_digest, field_name="reviewed evidence digest")
        if not isinstance(self.exact_evidence_matched, bool):
            raise ValueError("reviewed evidence match flag must be boolean")
        if (
            self.exact_evidence_matched
            and self.state is not ReviewedVersionState.IN_RANGE
        ):
            raise ValueError("exact reviewed evidence requires an in-range version")


@dataclass(frozen=True, slots=True)
class ExecutableObservationV1:
    """Content-free identity of the safely resolved executable."""

    executable_identity: str
    reported_version: str | None
    observed: bool

    def __post_init__(self) -> None:
        _validate_digest(
            self.executable_identity,
            field_name="executable identity",
        )
        if self.reported_version is not None:
            _validate_version(self.reported_version, field_name="reported version")
        if not isinstance(self.observed, bool):
            raise ValueError("executable observed flag must be boolean")
        if not self.observed and self.reported_version is not None:
            raise ValueError("unobserved executable cannot report a version")


@dataclass(frozen=True, slots=True)
class ProtocolNegotiationV1:
    """Content-free handshake and framing result."""

    protocol_family: str
    protocol_version: str | None
    state: ProtocolNegotiationState
    handshake_digest: str

    def __post_init__(self) -> None:
        _validate_identity(self.protocol_family, field_name="protocol family")
        if self.protocol_version is not None:
            _validate_version(self.protocol_version, field_name="protocol version")
        if not isinstance(self.state, ProtocolNegotiationState):
            raise ValueError("protocol negotiation state is invalid")
        _validate_digest(self.handshake_digest, field_name="protocol handshake digest")
        if (
            self.state
            in {
                ProtocolNegotiationState.CONFORMANT,
                ProtocolNegotiationState.MAJOR_MISMATCH,
            }
            and self.protocol_version is None
        ):
            raise ValueError("observed protocol state requires a protocol version")


@dataclass(frozen=True, slots=True)
class CapabilityAdmissionV1:
    """Required capabilities compared with one negotiated snapshot."""

    capability_fingerprint: str
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_digest(
            self.capability_fingerprint,
            field_name="capability fingerprint",
        )
        required = _normalize_identities(
            self.required_capabilities,
            field_name="required capabilities",
            maximum=MAX_COMPATIBILITY_CAPABILITIES,
        )
        missing = _normalize_identities(
            self.missing_capabilities,
            field_name="missing capabilities",
            maximum=MAX_COMPATIBILITY_CAPABILITIES,
        )
        if not set(missing).issubset(required):
            raise ValueError("missing capabilities must be required")
        object.__setattr__(self, "required_capabilities", required)
        object.__setattr__(self, "missing_capabilities", missing)


@dataclass(frozen=True, slots=True)
class KnownIncompatibilityV1:
    """One explicitly reviewed, digest-bound known-bad rule match."""

    rule_id: str
    reason_code: str
    evidence_digest: str

    def __post_init__(self) -> None:
        _validate_identity(self.rule_id, field_name="known-bad rule id")
        _validate_identity(self.reason_code, field_name="known-bad reason code")
        _validate_digest(
            self.evidence_digest,
            field_name="known-bad evidence digest",
        )


@dataclass(frozen=True, slots=True)
class SecurityCompatibilityV1:
    """Explicit security-invariant and known-bad evidence."""

    invariant_failures: tuple[str, ...] = ()
    known_incompatibilities: tuple[KnownIncompatibilityV1, ...] = ()

    def __post_init__(self) -> None:
        failures = _normalize_identities(
            self.invariant_failures,
            field_name="security invariant failures",
            maximum=MAX_COMPATIBILITY_REASONS,
        )
        if not all(
            isinstance(item, KnownIncompatibilityV1)
            for item in self.known_incompatibilities
        ):
            raise ValueError("known incompatibilities are invalid")
        normalized = tuple(
            sorted(self.known_incompatibilities, key=lambda item: item.rule_id)
        )
        if len({item.rule_id for item in normalized}) != len(normalized):
            raise ValueError("known incompatibility rule ids must be unique")
        object.__setattr__(self, "invariant_failures", failures)
        object.__setattr__(self, "known_incompatibilities", normalized)


@dataclass(frozen=True, slots=True)
class CompatibilityObservationV1:
    """Immutable decision evidence for one structured route probe."""

    agent_id: str
    route_id: str
    profile_digest: str
    executable: ExecutableObservationV1
    reviewed_version: ReviewedVersionEvidenceV1
    protocol: ProtocolNegotiationV1
    capabilities: CapabilityAdmissionV1
    security: SecurityCompatibilityV1
    status: CompatibilityStatus
    confidence: CompatibilityConfidence
    reason_codes: tuple[str, ...]
    cache_key_digest: str
    observed_at: datetime
    expires_at: datetime
    content_free: bool = True
    schema_version: int = COMPATIBILITY_OBSERVATION_SCHEMA_VERSION
    observation_id: str = field(init=False)
    probe_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != COMPATIBILITY_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported compatibility observation schema_version")
        _validate_identity(self.agent_id, field_name="agent id")
        _validate_identity(self.route_id, field_name="route id")
        _validate_digest(self.profile_digest, field_name="profile digest")
        for value, expected, label in (
            (self.executable, ExecutableObservationV1, "executable observation"),
            (self.reviewed_version, ReviewedVersionEvidenceV1, "version evidence"),
            (self.protocol, ProtocolNegotiationV1, "protocol negotiation"),
            (self.capabilities, CapabilityAdmissionV1, "capability admission"),
            (self.security, SecurityCompatibilityV1, "security compatibility"),
        ):
            if not isinstance(value, expected):
                raise ValueError(f"{label} is invalid")
        if not isinstance(self.status, CompatibilityStatus):
            raise ValueError("compatibility status is invalid")
        if not isinstance(self.confidence, CompatibilityConfidence):
            raise ValueError("compatibility confidence is invalid")
        reasons = _normalize_identities(
            self.reason_codes,
            field_name="compatibility reason codes",
            maximum=MAX_COMPATIBILITY_REASONS,
        )
        object.__setattr__(self, "reason_codes", reasons)
        _validate_digest(self.cache_key_digest, field_name="cache key digest")
        _validate_time_range(self.observed_at, self.expires_at)
        if self.content_free is not True:
            raise ValueError("compatibility observations must be content-free")
        _validate_status_evidence(self)
        payload = _observation_payload(self, include_identity=False)
        probe_digest = _canonical_digest(payload)
        object.__setattr__(self, "probe_digest", probe_digest)
        object.__setattr__(self, "observation_id", f"compat-{probe_digest[:24]}")

    @property
    def executable_identity(self) -> str:
        """Return the minimum-field executable identity projection."""
        return self.executable.executable_identity

    @property
    def reported_version(self) -> str | None:
        """Return the minimum-field reported version projection."""
        return self.executable.reported_version

    @property
    def protocol_family(self) -> str:
        """Return the minimum-field protocol family projection."""
        return self.protocol.protocol_family

    @property
    def protocol_version(self) -> str | None:
        """Return the minimum-field protocol version projection."""
        return self.protocol.protocol_version

    @property
    def capability_fingerprint(self) -> str:
        """Return the minimum-field capability fingerprint projection."""
        return self.capabilities.capability_fingerprint

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        """Return mandatory structured capabilities."""
        return self.capabilities.required_capabilities

    @property
    def missing_capabilities(self) -> tuple[str, ...]:
        """Return missing mandatory structured capabilities."""
        return self.capabilities.missing_capabilities

    @property
    def known_incompatibilities(self) -> tuple[KnownIncompatibilityV1, ...]:
        """Return matched digest-bound known-bad evidence."""
        return self.security.known_incompatibilities

    @property
    def native_eligible(self) -> bool:
        """Native passthrough remains independent of structured admission."""
        return self.executable.observed

    def structured_admitted(self, *, allow_unverified: bool) -> bool:
        """Apply the explicit policy switch for compatible-unverified routes."""
        if self.status is CompatibilityStatus.VERIFIED:
            return True
        return (
            self.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED
            and allow_unverified
            and not self.missing_capabilities
            and not self.security.invariant_failures
            and not self.known_incompatibilities
        )


def evaluate_compatibility(
    *,
    agent_id: str,
    route_id: str,
    profile_digest: str,
    executable: ExecutableObservationV1,
    reviewed_version: ReviewedVersionEvidenceV1,
    protocol: ProtocolNegotiationV1,
    capabilities: CapabilityAdmissionV1,
    security: SecurityCompatibilityV1,
    cache_key: CompatibilityProbeCacheKeyV1,
    observed_at: datetime,
    expires_at: datetime,
) -> CompatibilityObservationV1:
    """Derive status from conformance and explicit invariant evidence."""
    if (
        security.invariant_failures
        or protocol.state is ProtocolNegotiationState.MALFORMED
    ):
        status = CompatibilityStatus.UNSAFE
        confidence = CompatibilityConfidence.NONE
    elif (
        security.known_incompatibilities
        or protocol.state is ProtocolNegotiationState.MAJOR_MISMATCH
    ):
        status = CompatibilityStatus.INCOMPATIBLE
        confidence = CompatibilityConfidence.NONE
    elif not executable.observed:
        status = CompatibilityStatus.UNAVAILABLE
        confidence = CompatibilityConfidence.NONE
    elif (
        protocol.state is ProtocolNegotiationState.UNAVAILABLE
        or capabilities.missing_capabilities
    ):
        status = CompatibilityStatus.DEGRADED
        confidence = CompatibilityConfidence.LIMITED
    elif (
        reviewed_version.state is ReviewedVersionState.IN_RANGE
        and reviewed_version.exact_evidence_matched
    ):
        status = CompatibilityStatus.VERIFIED
        confidence = CompatibilityConfidence.REVIEWED
    else:
        status = CompatibilityStatus.COMPATIBLE_UNVERIFIED
        confidence = CompatibilityConfidence.OBSERVED
    reasons = _reason_codes(
        status=status,
        reviewed=reviewed_version,
        protocol=protocol,
        capabilities=capabilities,
        security=security,
    )
    return CompatibilityObservationV1(
        agent_id=agent_id,
        route_id=route_id,
        profile_digest=profile_digest,
        executable=executable,
        reviewed_version=reviewed_version,
        protocol=protocol,
        capabilities=capabilities,
        security=security,
        status=status,
        confidence=confidence,
        reason_codes=reasons,
        cache_key_digest=cache_key.digest,
        observed_at=observed_at,
        expires_at=expires_at,
    )


def compatibility_observation_to_dict(
    observation: CompatibilityObservationV1,
) -> dict[str, Any]:
    """Serialize one content-free observation into its stable public shape."""
    return _observation_payload(observation, include_identity=True)


def _reason_codes(
    *,
    status: CompatibilityStatus,
    reviewed: ReviewedVersionEvidenceV1,
    protocol: ProtocolNegotiationV1,
    capabilities: CapabilityAdmissionV1,
    security: SecurityCompatibilityV1,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    if status is CompatibilityStatus.VERIFIED:
        reasons.add("reviewed_conformance_verified")
    elif status is CompatibilityStatus.COMPATIBLE_UNVERIFIED:
        reasons.add("protocol_conformance_unverified")
        reasons.add(f"version_{reviewed.state.value}")
        if not reviewed.exact_evidence_matched:
            reasons.add("exact_reviewed_evidence_not_matched")
    if protocol.state is not ProtocolNegotiationState.CONFORMANT:
        reasons.add(f"protocol_{protocol.state.value}")
    reasons.update(
        f"missing_capability:{item}" for item in capabilities.missing_capabilities
    )
    reasons.update(f"security:{item}" for item in security.invariant_failures)
    reasons.update(
        f"known_bad:{item.rule_id}" for item in security.known_incompatibilities
    )
    if status is CompatibilityStatus.UNAVAILABLE:
        reasons.add("executable_unavailable")
    return tuple(sorted(reasons))


def _validate_status_evidence(observation: CompatibilityObservationV1) -> None:
    status = observation.status
    protocol = observation.protocol.state
    missing = observation.capabilities.missing_capabilities
    security = observation.security
    if status in {
        CompatibilityStatus.VERIFIED,
        CompatibilityStatus.COMPATIBLE_UNVERIFIED,
    }:
        if (
            not observation.executable.observed
            or protocol is not ProtocolNegotiationState.CONFORMANT
            or missing
            or security.invariant_failures
            or security.known_incompatibilities
        ):
            raise ValueError("compatible status requires complete conformance")
    if status is CompatibilityStatus.VERIFIED and (
        observation.reviewed_version.state is not ReviewedVersionState.IN_RANGE
        or not observation.reviewed_version.exact_evidence_matched
    ):
        raise ValueError("verified status requires exact reviewed evidence")
    if status is CompatibilityStatus.COMPATIBLE_UNVERIFIED and (
        observation.reviewed_version.state is ReviewedVersionState.IN_RANGE
        and observation.reviewed_version.exact_evidence_matched
    ):
        raise ValueError("unverified status cannot claim exact reviewed evidence")
    if status is CompatibilityStatus.INCOMPATIBLE and not (
        protocol is ProtocolNegotiationState.MAJOR_MISMATCH
        or security.known_incompatibilities
    ):
        raise ValueError("incompatible status requires explicit evidence")
    if status is CompatibilityStatus.UNSAFE and not (
        protocol is ProtocolNegotiationState.MALFORMED or security.invariant_failures
    ):
        raise ValueError("unsafe status requires an invariant failure")
    if status is CompatibilityStatus.UNAVAILABLE and observation.executable.observed:
        raise ValueError("unavailable status requires an unobserved executable")
    if status is CompatibilityStatus.DEGRADED and not (
        protocol is ProtocolNegotiationState.UNAVAILABLE or missing
    ):
        raise ValueError("degraded status requires protocol or capability loss")


def _observation_payload(
    observation: CompatibilityObservationV1,
    *,
    include_identity: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": observation.schema_version,
        "agent_id": observation.agent_id,
        "route_id": observation.route_id,
        "profile_digest": observation.profile_digest,
        "executable_identity": observation.executable_identity,
        "reported_version": observation.reported_version,
        "executable_observed": observation.executable.observed,
        "reviewed_version_state": observation.reviewed_version.state.value,
        "reviewed_evidence_digest": observation.reviewed_version.evidence_digest,
        "exact_reviewed_evidence_matched": (
            observation.reviewed_version.exact_evidence_matched
        ),
        "protocol_family": observation.protocol_family,
        "protocol_version": observation.protocol_version,
        "protocol_state": observation.protocol.state.value,
        "protocol_handshake_digest": observation.protocol.handshake_digest,
        "capability_fingerprint": observation.capability_fingerprint,
        "required_capabilities": list(observation.required_capabilities),
        "missing_capabilities": list(observation.missing_capabilities),
        "security_incompatibilities": list(observation.security.invariant_failures),
        "known_incompatibilities": [
            {
                "rule_id": item.rule_id,
                "reason_code": item.reason_code,
                "evidence_digest": item.evidence_digest,
            }
            for item in observation.known_incompatibilities
        ],
        "status": observation.status.value,
        "confidence": observation.confidence.value,
        "reason_codes": list(observation.reason_codes),
        "cache_key_digest": observation.cache_key_digest,
        "observed_at": observation.observed_at.isoformat(),
        "expires_at": observation.expires_at.isoformat(),
        "content_free": observation.content_free,
    }
    if include_identity:
        payload["observation_id"] = observation.observation_id
        payload["probe_digest"] = observation.probe_digest
    return payload


def _validate_time_range(observed_at: datetime, expires_at: datetime) -> None:
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    if expires_at <= observed_at:
        raise ValueError("compatibility observation expiry must follow observation")


def _normalize_identities(
    values: object,
    *,
    field_name: str,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError(f"{field_name} must be a bounded tuple")
    for value in values:
        _validate_identity(value, field_name=field_name)
    normalized = tuple(sorted(values))
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must be unique")
    return normalized


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_version(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _canonical_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
