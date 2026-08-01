"""Strict codecs for recovery and upgrade-radar evidence contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.compatibility import compatibility_observation_to_dict
from gigaloom.contracts.compatibility_codec import compatibility_observation_from_dict
from gigaloom.contracts.operational_evidence import (
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
    RecoveryReceiptV1,
    RouteEvidenceV1,
    UpgradeRadarReportV1,
    UpgradeRecommendation,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def operational_evidence_to_dict(value: OperationalEvidenceV1) -> dict[str, Any]:
    """Serialize one content-free evidence reference."""
    return {
        "schema_version": value.schema_version,
        "evidence_id": value.evidence_id,
        "kind": value.kind,
        "status": value.status.value,
        "evidence_digest": value.evidence_digest,
        "reason_code": value.reason_code,
        "source_digest": value.source_digest,
    }


def operational_evidence_from_dict(
    payload: Mapping[str, Any],
) -> OperationalEvidenceV1:
    """Decode one strict content-free evidence reference."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "evidence_id",
            "kind",
            "status",
            "evidence_digest",
            "reason_code",
            "source_digest",
        },
        field_name="operational evidence",
    )
    return OperationalEvidenceV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        evidence_id=_string(value["evidence_id"], "evidence_id"),
        kind=_string(value["kind"], "kind"),
        status=_enum(OperationalEvidenceStatus, value["status"], "status"),
        evidence_digest=_string(value["evidence_digest"], "evidence_digest"),
        reason_code=_string(value["reason_code"], "reason_code"),
        source_digest=_optional_string(value["source_digest"], "source_digest"),
    )


def route_evidence_to_dict(value: RouteEvidenceV1) -> dict[str, Any]:
    """Serialize one immutable route evidence binding."""
    return {
        "schema_version": value.schema_version,
        "route_id": value.route_id,
        "revision_digest": value.revision_digest,
        "capability_fingerprint": value.capability_fingerprint,
        "compatibility_observation_digest": value.compatibility_observation_digest,
    }


def route_evidence_from_dict(payload: Mapping[str, Any]) -> RouteEvidenceV1:
    """Decode one strict immutable route evidence binding."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "route_id",
            "revision_digest",
            "capability_fingerprint",
            "compatibility_observation_digest",
        },
        field_name="route evidence",
    )
    return RouteEvidenceV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        route_id=_string(value["route_id"], "route_id"),
        revision_digest=_string(value["revision_digest"], "revision_digest"),
        capability_fingerprint=_string(
            value["capability_fingerprint"],
            "capability_fingerprint",
        ),
        compatibility_observation_digest=_string(
            value["compatibility_observation_digest"],
            "compatibility_observation_digest",
        ),
    )


def recovery_receipt_to_dict(value: RecoveryReceiptV1) -> dict[str, Any]:
    """Serialize one content-free recovery receipt."""
    return {
        "schema_version": value.schema_version,
        "receipt_id": value.receipt_id,
        "data_root_fingerprint": value.data_root_fingerprint,
        "check_catalog_digest": value.check_catalog_digest,
        "started_at": value.started_at.isoformat(),
        "finished_at": value.finished_at.isoformat(),
        "checks": _evidence_to_wire(value.checks),
        "derived_rebuilds": _evidence_to_wire(value.derived_rebuilds),
        "quarantine_previews": _evidence_to_wire(value.quarantine_previews),
        "fault_fixture_ids": list(value.fault_fixture_ids),
        "invariants": _evidence_to_wire(value.invariants),
        "omissions": list(value.omissions),
        "content_free": value.content_free,
    }


def recovery_receipt_from_dict(payload: Mapping[str, Any]) -> RecoveryReceiptV1:
    """Decode one strict content-free recovery receipt."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "receipt_id",
            "data_root_fingerprint",
            "check_catalog_digest",
            "started_at",
            "finished_at",
            "checks",
            "derived_rebuilds",
            "quarantine_previews",
            "fault_fixture_ids",
            "invariants",
            "omissions",
            "content_free",
        },
        field_name="recovery receipt",
    )
    return RecoveryReceiptV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        receipt_id=_string(value["receipt_id"], "receipt_id"),
        data_root_fingerprint=_string(
            value["data_root_fingerprint"],
            "data_root_fingerprint",
        ),
        check_catalog_digest=_string(
            value["check_catalog_digest"],
            "check_catalog_digest",
        ),
        started_at=parse_timestamp(value["started_at"], field_name="started_at"),
        finished_at=parse_timestamp(value["finished_at"], field_name="finished_at"),
        checks=_evidence_from_wire(value["checks"], "checks"),
        derived_rebuilds=_evidence_from_wire(
            value["derived_rebuilds"],
            "derived_rebuilds",
        ),
        quarantine_previews=_evidence_from_wire(
            value["quarantine_previews"],
            "quarantine_previews",
        ),
        fault_fixture_ids=_string_tuple(
            value["fault_fixture_ids"],
            "fault_fixture_ids",
        ),
        invariants=_evidence_from_wire(value["invariants"], "invariants"),
        omissions=_string_tuple(value["omissions"], "omissions"),
        content_free=_boolean(value["content_free"], "content_free"),
    )


def upgrade_radar_report_to_dict(value: UpgradeRadarReportV1) -> dict[str, Any]:
    """Serialize one immutable recommendation-only upgrade report."""
    return {
        "schema_version": value.schema_version,
        "report_id": value.report_id,
        "sealed_corpus_digest": value.sealed_corpus_digest,
        "current_route": route_evidence_to_dict(value.current_route),
        "candidate_route": route_evidence_to_dict(value.candidate_route),
        "compatibility_observations": [
            compatibility_observation_to_dict(item)
            for item in value.compatibility_observations
        ],
        "capability_delta": _evidence_to_wire(value.capability_delta),
        "loss_delta": _evidence_to_wire(value.loss_delta),
        "gate_results": _evidence_to_wire(value.gate_results),
        "latency_observations": _evidence_to_wire(value.latency_observations),
        "usage_observations": _evidence_to_wire(value.usage_observations),
        "cost_observations": _evidence_to_wire(value.cost_observations),
        "uncertainty": list(value.uncertainty),
        "omissions": list(value.omissions),
        "recommendation": value.recommendation.value,
        "content_free": value.content_free,
    }


def upgrade_radar_report_from_dict(
    payload: Mapping[str, Any],
) -> UpgradeRadarReportV1:
    """Decode one strict recommendation-only upgrade report."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "report_id",
            "sealed_corpus_digest",
            "current_route",
            "candidate_route",
            "compatibility_observations",
            "capability_delta",
            "loss_delta",
            "gate_results",
            "latency_observations",
            "usage_observations",
            "cost_observations",
            "uncertainty",
            "omissions",
            "recommendation",
            "content_free",
        },
        field_name="upgrade radar report",
    )
    observations = _object_array(
        value["compatibility_observations"],
        "compatibility_observations",
    )
    return UpgradeRadarReportV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        report_id=_string(value["report_id"], "report_id"),
        sealed_corpus_digest=_string(
            value["sealed_corpus_digest"],
            "sealed_corpus_digest",
        ),
        current_route=route_evidence_from_dict(
            _mapping(value["current_route"], "current_route")
        ),
        candidate_route=route_evidence_from_dict(
            _mapping(value["candidate_route"], "candidate_route")
        ),
        compatibility_observations=tuple(
            compatibility_observation_from_dict(item) for item in observations
        ),
        capability_delta=_evidence_from_wire(
            value["capability_delta"],
            "capability_delta",
        ),
        loss_delta=_evidence_from_wire(value["loss_delta"], "loss_delta"),
        gate_results=_evidence_from_wire(value["gate_results"], "gate_results"),
        latency_observations=_evidence_from_wire(
            value["latency_observations"],
            "latency_observations",
        ),
        usage_observations=_evidence_from_wire(
            value["usage_observations"],
            "usage_observations",
        ),
        cost_observations=_evidence_from_wire(
            value["cost_observations"],
            "cost_observations",
        ),
        uncertainty=_string_tuple(value["uncertainty"], "uncertainty"),
        omissions=_string_tuple(value["omissions"], "omissions"),
        recommendation=_enum(
            UpgradeRecommendation,
            value["recommendation"],
            "recommendation",
        ),
        content_free=_boolean(value["content_free"], "content_free"),
    )


def _evidence_to_wire(
    values: tuple[OperationalEvidenceV1, ...],
) -> list[dict[str, Any]]:
    return [operational_evidence_to_dict(item) for item in values]


def _evidence_from_wire(
    value: object,
    field_name: str,
) -> tuple[OperationalEvidenceV1, ...]:
    return tuple(
        operational_evidence_from_dict(item)
        for item in _object_array(value, field_name)
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
