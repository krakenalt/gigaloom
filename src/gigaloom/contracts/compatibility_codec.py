"""Strict decoder for flattened compatibility observation wire objects."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.compatibility import (
    CapabilityAdmissionV1,
    CompatibilityConfidence,
    CompatibilityObservationV1,
    CompatibilityStatus,
    ExecutableObservationV1,
    KnownIncompatibilityV1,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    SecurityCompatibilityV1,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def compatibility_observation_from_dict(
    payload: Mapping[str, Any],
) -> CompatibilityObservationV1:
    """Decode and verify one strict content-free observation."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "agent_id",
            "route_id",
            "profile_digest",
            "executable_identity",
            "reported_version",
            "executable_observed",
            "reviewed_version_state",
            "reviewed_evidence_digest",
            "exact_reviewed_evidence_matched",
            "protocol_family",
            "protocol_version",
            "protocol_state",
            "protocol_handshake_digest",
            "capability_fingerprint",
            "required_capabilities",
            "missing_capabilities",
            "security_incompatibilities",
            "known_incompatibilities",
            "status",
            "confidence",
            "reason_codes",
            "cache_key_digest",
            "observed_at",
            "expires_at",
            "content_free",
            "observation_id",
            "probe_digest",
        },
        field_name="compatibility observation",
    )
    known_payloads = _object_array(
        value["known_incompatibilities"],
        "known_incompatibilities",
    )
    observation = CompatibilityObservationV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        agent_id=_string(value["agent_id"], "agent_id"),
        route_id=_string(value["route_id"], "route_id"),
        profile_digest=_string(value["profile_digest"], "profile_digest"),
        executable=ExecutableObservationV1(
            executable_identity=_string(
                value["executable_identity"],
                "executable_identity",
            ),
            reported_version=_optional_string(
                value["reported_version"],
                "reported_version",
            ),
            observed=_boolean(value["executable_observed"], "executable_observed"),
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=_enum(
                ReviewedVersionState,
                value["reviewed_version_state"],
                "reviewed_version_state",
            ),
            evidence_digest=_string(
                value["reviewed_evidence_digest"],
                "reviewed_evidence_digest",
            ),
            exact_evidence_matched=_boolean(
                value["exact_reviewed_evidence_matched"],
                "exact_reviewed_evidence_matched",
            ),
        ),
        protocol=ProtocolNegotiationV1(
            protocol_family=_string(value["protocol_family"], "protocol_family"),
            protocol_version=_optional_string(
                value["protocol_version"],
                "protocol_version",
            ),
            state=_enum(
                ProtocolNegotiationState,
                value["protocol_state"],
                "protocol_state",
            ),
            handshake_digest=_string(
                value["protocol_handshake_digest"],
                "protocol_handshake_digest",
            ),
        ),
        capabilities=CapabilityAdmissionV1(
            capability_fingerprint=_string(
                value["capability_fingerprint"],
                "capability_fingerprint",
            ),
            required_capabilities=_string_tuple(
                value["required_capabilities"],
                "required_capabilities",
            ),
            missing_capabilities=_string_tuple(
                value["missing_capabilities"],
                "missing_capabilities",
            ),
        ),
        security=SecurityCompatibilityV1(
            invariant_failures=_string_tuple(
                value["security_incompatibilities"],
                "security_incompatibilities",
            ),
            known_incompatibilities=tuple(
                _known_incompatibility(item) for item in known_payloads
            ),
        ),
        status=_enum(CompatibilityStatus, value["status"], "status"),
        confidence=_enum(CompatibilityConfidence, value["confidence"], "confidence"),
        reason_codes=_string_tuple(value["reason_codes"], "reason_codes"),
        cache_key_digest=_string(value["cache_key_digest"], "cache_key_digest"),
        observed_at=parse_timestamp(value["observed_at"], field_name="observed_at"),
        expires_at=parse_timestamp(value["expires_at"], field_name="expires_at"),
        content_free=_boolean(value["content_free"], "content_free"),
    )
    if value["observation_id"] != observation.observation_id:
        raise ValueError("compatibility observation id does not match its fields")
    if value["probe_digest"] != observation.probe_digest:
        raise ValueError("compatibility probe digest does not match its fields")
    return observation


def _known_incompatibility(payload: Mapping[str, Any]) -> KnownIncompatibilityV1:
    value = require_mapping(
        payload,
        required={"rule_id", "reason_code", "evidence_digest"},
        field_name="known incompatibility",
    )
    return KnownIncompatibilityV1(
        rule_id=_string(value["rule_id"], "rule_id"),
        reason_code=_string(value["reason_code"], "reason_code"),
        evidence_digest=_string(value["evidence_digest"], "evidence_digest"),
    )


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error


def _object_array(value: object, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise ValueError(f"{field_name} must be an array of objects")
    return tuple(cast(Mapping[str, Any], item) for item in value)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(cast(str, item) for item in value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value
