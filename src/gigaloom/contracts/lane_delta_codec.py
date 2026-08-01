"""Strict codecs for lane-delta continuation packet contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.lane_delta import (
    AttachmentBindingStatus,
    LaneAttachmentBindingV1,
    LaneContentMode,
    LaneDeltaPacketV1,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTruncationV1,
    LaneTurnRangeV1,
)
from gigaloom.contracts.operational_evidence_codec import (
    operational_evidence_from_dict,
    operational_evidence_to_dict,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def lane_identity_to_dict(value: LaneIdentityV1) -> dict[str, Any]:
    """Serialize one mechanical lane identity."""
    return {
        "schema_version": value.schema_version,
        "agent_id": value.agent_id,
        "route_id": value.route_id,
        "model_id": value.model_id,
        "account_ref": value.account_ref,
        "workspace_fingerprint": value.workspace_fingerprint,
        "policy_digest": value.policy_digest,
        "context_manifest_digest": value.context_manifest_digest,
        "run_capsule_digest": value.run_capsule_digest,
        "session_id": value.session_id,
        "lane_digest": value.lane_digest,
    }


def lane_identity_from_dict(payload: Mapping[str, Any]) -> LaneIdentityV1:
    """Decode and verify one mechanical lane identity."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "agent_id",
            "route_id",
            "model_id",
            "account_ref",
            "workspace_fingerprint",
            "policy_digest",
            "context_manifest_digest",
            "run_capsule_digest",
            "session_id",
            "lane_digest",
        },
        field_name="lane identity",
    )
    identity = LaneIdentityV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        agent_id=_string(value["agent_id"], "agent_id"),
        route_id=_string(value["route_id"], "route_id"),
        model_id=_string(value["model_id"], "model_id"),
        account_ref=_string(value["account_ref"], "account_ref"),
        workspace_fingerprint=_string(
            value["workspace_fingerprint"],
            "workspace_fingerprint",
        ),
        policy_digest=_string(value["policy_digest"], "policy_digest"),
        context_manifest_digest=_string(
            value["context_manifest_digest"],
            "context_manifest_digest",
        ),
        run_capsule_digest=_string(
            value["run_capsule_digest"],
            "run_capsule_digest",
        ),
        session_id=_optional_string(value["session_id"], "session_id"),
    )
    if value["lane_digest"] != identity.lane_digest:
        raise ValueError("lane identity digest does not match its fields")
    return identity


def lane_turn_range_to_dict(value: LaneTurnRangeV1) -> dict[str, Any]:
    """Serialize one contiguous missed turn range."""
    return {
        "schema_version": value.schema_version,
        "first_sequence": value.first_sequence,
        "last_sequence": value.last_sequence,
    }


def lane_turn_range_from_dict(payload: Mapping[str, Any]) -> LaneTurnRangeV1:
    """Decode one contiguous missed turn range."""
    value = require_mapping(
        payload,
        required={"schema_version", "first_sequence", "last_sequence"},
        field_name="lane turn range",
    )
    return LaneTurnRangeV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        first_sequence=_integer(value["first_sequence"], "first_sequence"),
        last_sequence=_integer(value["last_sequence"], "last_sequence"),
    )


def lane_attachment_to_dict(value: LaneAttachmentBindingV1) -> dict[str, Any]:
    """Serialize one attachment availability binding."""
    return {
        "schema_version": value.schema_version,
        "attachment_id": value.attachment_id,
        "expected_digest": value.expected_digest,
        "observed_digest": value.observed_digest,
        "status": value.status.value,
    }


def lane_attachment_from_dict(
    payload: Mapping[str, Any],
) -> LaneAttachmentBindingV1:
    """Decode one strict attachment availability binding."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "attachment_id",
            "expected_digest",
            "observed_digest",
            "status",
        },
        field_name="lane attachment binding",
    )
    return LaneAttachmentBindingV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        attachment_id=_string(value["attachment_id"], "attachment_id"),
        expected_digest=_string(value["expected_digest"], "expected_digest"),
        observed_digest=_optional_string(
            value["observed_digest"],
            "observed_digest",
        ),
        status=_enum(AttachmentBindingStatus, value["status"], "status"),
    )


def lane_truncation_to_dict(value: LaneTruncationV1) -> dict[str, Any]:
    """Serialize explicit delta truncation evidence."""
    return {
        "schema_version": value.schema_version,
        "truncated": value.truncated,
        "original_count": value.original_count,
        "included_count": value.included_count,
        "reason_code": value.reason_code,
    }


def lane_truncation_from_dict(payload: Mapping[str, Any]) -> LaneTruncationV1:
    """Decode strict delta truncation evidence."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "truncated",
            "original_count",
            "included_count",
            "reason_code",
        },
        field_name="lane truncation",
    )
    return LaneTruncationV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        truncated=_boolean(value["truncated"], "truncated"),
        original_count=_integer(value["original_count"], "original_count"),
        included_count=_integer(value["included_count"], "included_count"),
        reason_code=_optional_string(value["reason_code"], "reason_code"),
    )


def lane_delta_packet_to_dict(value: LaneDeltaPacketV1) -> dict[str, Any]:
    """Serialize one bounded lane-delta packet."""
    return {
        "schema_version": value.schema_version,
        "packet_id": value.packet_id,
        "source_lane": lane_identity_to_dict(value.source_lane),
        "destination_lane": lane_identity_to_dict(value.destination_lane),
        "last_shared_turn": value.last_shared_turn,
        "missed_turn_range": lane_turn_range_to_dict(value.missed_turn_range),
        "context_manifest_digests": list(value.context_manifest_digests),
        "run_capsule_digests": list(value.run_capsule_digests),
        "changed_anchors": [
            operational_evidence_to_dict(item) for item in value.changed_anchors
        ],
        "attachment_bindings": [
            lane_attachment_to_dict(item) for item in value.attachment_bindings
        ],
        "disclosure_mode": value.disclosure_mode.value,
        "content_mode": value.content_mode.value,
        "truncation": lane_truncation_to_dict(value.truncation),
        "omissions": list(value.omissions),
        "created_at": value.created_at.isoformat(),
    }


def lane_delta_packet_from_dict(payload: Mapping[str, Any]) -> LaneDeltaPacketV1:
    """Decode one strict bounded lane-delta packet."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "packet_id",
            "source_lane",
            "destination_lane",
            "last_shared_turn",
            "missed_turn_range",
            "context_manifest_digests",
            "run_capsule_digests",
            "changed_anchors",
            "attachment_bindings",
            "disclosure_mode",
            "content_mode",
            "truncation",
            "omissions",
            "created_at",
        },
        field_name="lane delta packet",
    )
    return LaneDeltaPacketV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        packet_id=_string(value["packet_id"], "packet_id"),
        source_lane=lane_identity_from_dict(
            _mapping(value["source_lane"], "source_lane")
        ),
        destination_lane=lane_identity_from_dict(
            _mapping(value["destination_lane"], "destination_lane")
        ),
        last_shared_turn=_string(value["last_shared_turn"], "last_shared_turn"),
        missed_turn_range=lane_turn_range_from_dict(
            _mapping(value["missed_turn_range"], "missed_turn_range")
        ),
        context_manifest_digests=_string_tuple(
            value["context_manifest_digests"],
            "context_manifest_digests",
        ),
        run_capsule_digests=_string_tuple(
            value["run_capsule_digests"],
            "run_capsule_digests",
        ),
        changed_anchors=tuple(
            operational_evidence_from_dict(item)
            for item in _object_array(value["changed_anchors"], "changed_anchors")
        ),
        attachment_bindings=tuple(
            lane_attachment_from_dict(item)
            for item in _object_array(
                value["attachment_bindings"],
                "attachment_bindings",
            )
        ),
        disclosure_mode=_enum(
            LaneDisclosureMode,
            value["disclosure_mode"],
            "disclosure_mode",
        ),
        content_mode=_enum(
            LaneContentMode,
            value["content_mode"],
            "content_mode",
        ),
        truncation=lane_truncation_from_dict(
            _mapping(value["truncation"], "truncation")
        ),
        omissions=_string_tuple(value["omissions"], "omissions"),
        created_at=parse_timestamp(value["created_at"], field_name="created_at"),
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


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return cast(Mapping[str, Any], value)


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
