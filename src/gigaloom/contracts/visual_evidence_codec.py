"""Strict codecs for reusable visual gate receipt contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.operational_evidence_codec import (
    operational_evidence_from_dict,
    operational_evidence_to_dict,
)
from gigaloom.contracts.operational_validation import require_mapping
from gigaloom.contracts.visual_evidence import (
    VisualArtifactReferenceV1,
    VisualEvidenceSummaryV1,
    VisualGateReceiptV1,
    VisualGateStatus,
    VisualTolerancePolicyV1,
    VisualViewportV1,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def visual_viewport_to_dict(value: VisualViewportV1) -> dict[str, Any]:
    """Serialize one exact viewport."""
    return {
        "schema_version": value.schema_version,
        "viewport_id": value.viewport_id,
        "width": value.width,
        "height": value.height,
        "device_scale_factor": value.device_scale_factor,
    }


def visual_viewport_from_dict(payload: Mapping[str, Any]) -> VisualViewportV1:
    """Decode one exact viewport."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "viewport_id",
            "width",
            "height",
            "device_scale_factor",
        },
        field_name="visual viewport",
    )
    return VisualViewportV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        viewport_id=_string(value["viewport_id"], "viewport_id"),
        width=_integer(value["width"], "width"),
        height=_integer(value["height"], "height"),
        device_scale_factor=_number(
            value["device_scale_factor"],
            "device_scale_factor",
        ),
    )


def visual_summary_to_dict(value: VisualEvidenceSummaryV1) -> dict[str, Any]:
    """Serialize one bounded console/request summary."""
    return {
        "schema_version": value.schema_version,
        "total_count": value.total_count,
        "failure_count": value.failure_count,
        "evidence_digest": value.evidence_digest,
        "omissions": list(value.omissions),
    }


def visual_summary_from_dict(payload: Mapping[str, Any]) -> VisualEvidenceSummaryV1:
    """Decode one bounded console/request summary."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "total_count",
            "failure_count",
            "evidence_digest",
            "omissions",
        },
        field_name="visual evidence summary",
    )
    return VisualEvidenceSummaryV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        total_count=_integer(value["total_count"], "total_count"),
        failure_count=_integer(value["failure_count"], "failure_count"),
        evidence_digest=_string(value["evidence_digest"], "evidence_digest"),
        omissions=_string_tuple(value["omissions"], "omissions"),
    )


def visual_artifact_to_dict(value: VisualArtifactReferenceV1) -> dict[str, Any]:
    """Serialize one redacted screenshot reference."""
    return {
        "schema_version": value.schema_version,
        "artifact_id": value.artifact_id,
        "viewport_id": value.viewport_id,
        "relative_path": value.relative_path,
        "artifact_digest": value.artifact_digest,
        "media_type": value.media_type,
        "byte_count": value.byte_count,
        "redacted": value.redacted,
    }


def visual_artifact_from_dict(
    payload: Mapping[str, Any],
) -> VisualArtifactReferenceV1:
    """Decode one strict redacted screenshot reference."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "artifact_id",
            "viewport_id",
            "relative_path",
            "artifact_digest",
            "media_type",
            "byte_count",
            "redacted",
        },
        field_name="visual artifact",
    )
    return VisualArtifactReferenceV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        artifact_id=_string(value["artifact_id"], "artifact_id"),
        viewport_id=_string(value["viewport_id"], "viewport_id"),
        relative_path=_string(value["relative_path"], "relative_path"),
        artifact_digest=_string(value["artifact_digest"], "artifact_digest"),
        media_type=_string(value["media_type"], "media_type"),
        byte_count=_integer(value["byte_count"], "byte_count"),
        redacted=_boolean(value["redacted"], "redacted"),
    )


def visual_tolerance_to_dict(value: VisualTolerancePolicyV1) -> dict[str, Any]:
    """Serialize one explicit tolerance policy."""
    return {
        "schema_version": value.schema_version,
        "policy_digest": value.policy_digest,
        "max_console_errors": value.max_console_errors,
        "max_request_failures": value.max_request_failures,
        "max_overflow_pixels": value.max_overflow_pixels,
        "max_timing_variance_ms": value.max_timing_variance_ms,
        "max_attempts": value.max_attempts,
        "required_passes": value.required_passes,
    }


def visual_tolerance_from_dict(
    payload: Mapping[str, Any],
) -> VisualTolerancePolicyV1:
    """Decode one explicit tolerance policy."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "policy_digest",
            "max_console_errors",
            "max_request_failures",
            "max_overflow_pixels",
            "max_timing_variance_ms",
            "max_attempts",
            "required_passes",
        },
        field_name="visual tolerance policy",
    )
    return VisualTolerancePolicyV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        policy_digest=_string(value["policy_digest"], "policy_digest"),
        max_console_errors=_integer(
            value["max_console_errors"],
            "max_console_errors",
        ),
        max_request_failures=_integer(
            value["max_request_failures"],
            "max_request_failures",
        ),
        max_overflow_pixels=_integer(
            value["max_overflow_pixels"],
            "max_overflow_pixels",
        ),
        max_timing_variance_ms=_integer(
            value["max_timing_variance_ms"],
            "max_timing_variance_ms",
        ),
        max_attempts=_integer(value["max_attempts"], "max_attempts"),
        required_passes=_integer(value["required_passes"], "required_passes"),
    )


def visual_gate_receipt_to_dict(value: VisualGateReceiptV1) -> dict[str, Any]:
    """Serialize one reusable visual gate receipt."""
    return {
        "schema_version": value.schema_version,
        "gate_id": value.gate_id,
        "origin": value.origin,
        "origin_policy_digest": value.origin_policy_digest,
        "source_revision": value.source_revision,
        "browser_fingerprint": value.browser_fingerprint,
        "viewports": [visual_viewport_to_dict(item) for item in value.viewports],
        "assertions": [operational_evidence_to_dict(item) for item in value.assertions],
        "console_summary": visual_summary_to_dict(value.console_summary),
        "request_summary": visual_summary_to_dict(value.request_summary),
        "screenshot_artifacts": [
            visual_artifact_to_dict(item) for item in value.screenshot_artifacts
        ],
        "redaction_receipt": operational_evidence_to_dict(value.redaction_receipt),
        "tolerance_policy": visual_tolerance_to_dict(value.tolerance_policy),
        "status": value.status.value,
    }


def visual_gate_receipt_from_dict(
    payload: Mapping[str, Any],
) -> VisualGateReceiptV1:
    """Decode one strict reusable visual gate receipt."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "gate_id",
            "origin",
            "origin_policy_digest",
            "source_revision",
            "browser_fingerprint",
            "viewports",
            "assertions",
            "console_summary",
            "request_summary",
            "screenshot_artifacts",
            "redaction_receipt",
            "tolerance_policy",
            "status",
        },
        field_name="visual gate receipt",
    )
    return VisualGateReceiptV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        gate_id=_string(value["gate_id"], "gate_id"),
        origin=_string(value["origin"], "origin"),
        origin_policy_digest=_string(
            value["origin_policy_digest"],
            "origin_policy_digest",
        ),
        source_revision=_string(value["source_revision"], "source_revision"),
        browser_fingerprint=_string(
            value["browser_fingerprint"],
            "browser_fingerprint",
        ),
        viewports=tuple(
            visual_viewport_from_dict(item)
            for item in _object_array(value["viewports"], "viewports")
        ),
        assertions=tuple(
            operational_evidence_from_dict(item)
            for item in _object_array(value["assertions"], "assertions")
        ),
        console_summary=visual_summary_from_dict(
            _mapping(value["console_summary"], "console_summary")
        ),
        request_summary=visual_summary_from_dict(
            _mapping(value["request_summary"], "request_summary")
        ),
        screenshot_artifacts=tuple(
            visual_artifact_from_dict(item)
            for item in _object_array(
                value["screenshot_artifacts"],
                "screenshot_artifacts",
            )
        ),
        redaction_receipt=operational_evidence_from_dict(
            _mapping(value["redaction_receipt"], "redaction_receipt")
        ),
        tolerance_policy=visual_tolerance_from_dict(
            _mapping(value["tolerance_policy"], "tolerance_policy")
        ),
        status=_enum(VisualGateStatus, value["status"], "status"),
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


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    return float(value)


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value
