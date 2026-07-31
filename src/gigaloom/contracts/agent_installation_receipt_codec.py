"""Strict codecs for managed-agent installation transaction receipts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.agent_installation import (
    AgentCleanupStatus,
    AgentInstallationOutcome,
    AgentInstallationReceiptV1,
    ExtractionLimitsV1,
    InstallationTransitionV1,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def extraction_limits_to_dict(value: ExtractionLimitsV1) -> dict[str, Any]:
    """Serialize exact archive/package extraction limits."""
    return {
        "schema_version": value.schema_version,
        "max_bytes": value.max_bytes,
        "max_files": value.max_files,
        "max_path_depth": value.max_path_depth,
    }


def extraction_limits_from_dict(payload: Mapping[str, Any]) -> ExtractionLimitsV1:
    """Decode exact archive/package extraction limits."""
    value = require_mapping(
        payload,
        required={"schema_version", "max_bytes", "max_files", "max_path_depth"},
        field_name="extraction limits",
    )
    return ExtractionLimitsV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        max_bytes=_integer(value["max_bytes"], "max_bytes"),
        max_files=_integer(value["max_files"], "max_files"),
        max_path_depth=_integer(value["max_path_depth"], "max_path_depth"),
    )


def installation_transition_to_dict(
    value: InstallationTransitionV1,
) -> dict[str, Any]:
    """Serialize one content-free installation state transition."""
    return {
        "schema_version": value.schema_version,
        "sequence": value.sequence,
        "state": value.state,
        "timestamp": value.timestamp.isoformat(),
        "evidence_digest": value.evidence_digest,
        "reason_code": value.reason_code,
    }


def installation_transition_from_dict(
    payload: Mapping[str, Any],
) -> InstallationTransitionV1:
    """Decode one strict installation state transition."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "sequence",
            "state",
            "timestamp",
            "evidence_digest",
            "reason_code",
        },
        field_name="installation transition",
    )
    return InstallationTransitionV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        sequence=_integer(value["sequence"], "sequence"),
        state=_string(value["state"], "state"),
        timestamp=parse_timestamp(value["timestamp"], field_name="timestamp"),
        evidence_digest=_string(value["evidence_digest"], "evidence_digest"),
        reason_code=_string(value["reason_code"], "reason_code"),
    )


def agent_installation_receipt_to_dict(
    value: AgentInstallationReceiptV1,
) -> dict[str, Any]:
    """Serialize one terminal, content-free installation receipt."""
    return {
        "schema_version": value.schema_version,
        "receipt_id": value.receipt_id,
        "plan_id": value.plan_id,
        "install_id": value.install_id,
        "registry_id": value.registry_id,
        "entry_digest": value.entry_digest,
        "snapshot_digest": value.snapshot_digest,
        "transitions": [
            installation_transition_to_dict(item) for item in value.transitions
        ],
        "bytes_received": value.bytes_received,
        "artifact_digest": value.artifact_digest,
        "package_integrity": value.package_integrity,
        "extraction_limits": extraction_limits_to_dict(value.extraction_limits),
        "probe_observation_digest": value.probe_observation_digest,
        "activation_id": value.activation_id,
        "rollback_install_id": value.rollback_install_id,
        "omissions": list(value.omissions),
        "cleanup_status": value.cleanup_status.value,
        "outcome": value.outcome.value,
        "started_at": value.started_at.isoformat(),
        "finished_at": value.finished_at.isoformat(),
        "content_free": value.content_free,
    }


def agent_installation_receipt_from_dict(
    payload: Mapping[str, Any],
) -> AgentInstallationReceiptV1:
    """Decode one strict terminal installation receipt."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "receipt_id",
            "plan_id",
            "install_id",
            "registry_id",
            "entry_digest",
            "snapshot_digest",
            "transitions",
            "bytes_received",
            "artifact_digest",
            "package_integrity",
            "extraction_limits",
            "probe_observation_digest",
            "activation_id",
            "rollback_install_id",
            "omissions",
            "cleanup_status",
            "outcome",
            "started_at",
            "finished_at",
            "content_free",
        },
        field_name="agent installation receipt",
    )
    transition_payloads = _object_array(value["transitions"], "transitions")
    return AgentInstallationReceiptV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        receipt_id=_string(value["receipt_id"], "receipt_id"),
        plan_id=_string(value["plan_id"], "plan_id"),
        install_id=_optional_string(value["install_id"], "install_id"),
        registry_id=_string(value["registry_id"], "registry_id"),
        entry_digest=_string(value["entry_digest"], "entry_digest"),
        snapshot_digest=_string(value["snapshot_digest"], "snapshot_digest"),
        transitions=tuple(
            installation_transition_from_dict(item) for item in transition_payloads
        ),
        bytes_received=_integer(value["bytes_received"], "bytes_received"),
        artifact_digest=_optional_string(
            value["artifact_digest"],
            "artifact_digest",
        ),
        package_integrity=_optional_string(
            value["package_integrity"],
            "package_integrity",
        ),
        extraction_limits=extraction_limits_from_dict(
            _mapping(value["extraction_limits"], "extraction_limits")
        ),
        probe_observation_digest=_optional_string(
            value["probe_observation_digest"],
            "probe_observation_digest",
        ),
        activation_id=_optional_string(value["activation_id"], "activation_id"),
        rollback_install_id=_optional_string(
            value["rollback_install_id"],
            "rollback_install_id",
        ),
        omissions=_string_tuple(value["omissions"], "omissions"),
        cleanup_status=_enum(
            AgentCleanupStatus,
            value["cleanup_status"],
            "cleanup_status",
        ),
        outcome=_enum(AgentInstallationOutcome, value["outcome"], "outcome"),
        started_at=parse_timestamp(value["started_at"], field_name="started_at"),
        finished_at=parse_timestamp(value["finished_at"], field_name="finished_at"),
        content_free=_boolean(value["content_free"], "content_free"),
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
