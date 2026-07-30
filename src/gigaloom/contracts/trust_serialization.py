"""Strict JSON-compatible serialization for source-to-sink contracts."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from gigaloom.contracts.trust import (
    DataFlowReceipt,
    InfluenceSet,
    ProvenanceClass,
    Sensitivity,
    SinkDecision,
    SinkDecisionReason,
    SinkDecisionStatus,
    SinkKind,
    SinkRequest,
    SourceRef,
    TrustClass,
)


def source_ref_to_dict(source: SourceRef) -> dict[str, Any]:
    """Serialize one source without source content."""
    return {
        "schema_version": source.schema_version,
        "source_id": source.source_id,
        "provenance": source.provenance.value if source.provenance else None,
        "trust": source.trust.value,
        "sensitivity": source.sensitivity.value,
        "content_sha256": source.content_sha256,
    }


def source_ref_from_dict(data: Mapping[str, Any]) -> SourceRef:
    """Parse one strict source reference and reject future enum/schema values."""
    _require_fields(
        data,
        {
            "schema_version",
            "source_id",
            "provenance",
            "trust",
            "sensitivity",
            "content_sha256",
        },
        field_name="source",
    )
    provenance = data["provenance"]
    return SourceRef(
        schema_version=_required_int(
            data["schema_version"], field_name="schema_version"
        ),
        source_id=_required_text(data["source_id"], field_name="source_id"),
        provenance=(
            None
            if provenance is None
            else _required_enum(
                provenance,
                ProvenanceClass,
                field_name="provenance",
            )
        ),
        trust=_required_enum(data["trust"], TrustClass, field_name="trust"),
        sensitivity=_required_enum(
            data["sensitivity"],
            Sensitivity,
            field_name="sensitivity",
        ),
        content_sha256=_required_text(
            data["content_sha256"],
            field_name="content_sha256",
        ),
    )


def sink_request_to_dict(request: SinkRequest) -> dict[str, Any]:
    """Serialize a bounded request without raw payload or secret metadata."""
    return {
        "schema_version": request.schema_version,
        "request_id": request.request_id,
        "sink_kind": request.sink_kind.value,
        "destination_metadata": request.destination_metadata,
        "destination_sha256": request.destination_sha256,
        "approved_destination_sha256": request.approved_destination_sha256,
        "payload_sha256": request.payload_sha256,
        "approved_payload_sha256": request.approved_payload_sha256,
        "payload_preview": request.payload_preview,
        "payload_sensitivity": request.payload_sensitivity.value,
        "metadata_sensitivity": request.metadata_sensitivity.value,
        "influence": [
            source_ref_to_dict(source) for source in request.influence.sources
        ],
        "destination_source_ids": list(request.destination_source_ids),
        "payload_source_ids": list(request.payload_source_ids),
        "redirect_destination_sha256": request.redirect_destination_sha256,
        "redirect_requires_revalidation": request.redirect_requires_revalidation,
    }


def sink_request_from_dict(data: Mapping[str, Any]) -> SinkRequest:
    """Parse one strict bounded request."""
    fields = {
        "schema_version",
        "request_id",
        "sink_kind",
        "destination_metadata",
        "destination_sha256",
        "approved_destination_sha256",
        "payload_sha256",
        "approved_payload_sha256",
        "payload_preview",
        "payload_sensitivity",
        "metadata_sensitivity",
        "influence",
        "destination_source_ids",
        "payload_source_ids",
        "redirect_destination_sha256",
        "redirect_requires_revalidation",
    }
    _require_fields(data, fields, field_name="sink request")
    raw_influence = _required_list(data["influence"], field_name="influence")
    return SinkRequest(
        schema_version=_required_int(
            data["schema_version"], field_name="schema_version"
        ),
        request_id=_required_text(data["request_id"], field_name="request_id"),
        sink_kind=_required_enum(
            data["sink_kind"],
            SinkKind,
            field_name="sink_kind",
        ),
        destination_metadata=_required_text(
            data["destination_metadata"],
            field_name="destination_metadata",
        ),
        destination_sha256=_required_text(
            data["destination_sha256"],
            field_name="destination_sha256",
        ),
        approved_destination_sha256=_required_text(
            data["approved_destination_sha256"],
            field_name="approved_destination_sha256",
        ),
        payload_sha256=_required_text(
            data["payload_sha256"],
            field_name="payload_sha256",
        ),
        approved_payload_sha256=_required_text(
            data["approved_payload_sha256"],
            field_name="approved_payload_sha256",
        ),
        payload_preview=_required_string(
            data["payload_preview"],
            field_name="payload_preview",
        ),
        payload_sensitivity=_required_enum(
            data["payload_sensitivity"],
            Sensitivity,
            field_name="payload_sensitivity",
        ),
        metadata_sensitivity=_required_enum(
            data["metadata_sensitivity"],
            Sensitivity,
            field_name="metadata_sensitivity",
        ),
        influence=InfluenceSet(
            tuple(
                source_ref_from_dict(_required_mapping(item, field_name="source"))
                for item in raw_influence
            )
        ),
        destination_source_ids=_text_tuple(
            data["destination_source_ids"],
            field_name="destination_source_ids",
        ),
        payload_source_ids=_text_tuple(
            data["payload_source_ids"],
            field_name="payload_source_ids",
        ),
        redirect_destination_sha256=_optional_text(
            data["redirect_destination_sha256"],
            field_name="redirect_destination_sha256",
        ),
        redirect_requires_revalidation=_required_bool(
            data["redirect_requires_revalidation"],
            field_name="redirect_requires_revalidation",
        ),
    )


def sink_request_digest(request: SinkRequest) -> str:
    """Return the stable digest binding all admission-relevant request fields."""
    return _canonical_hash(sink_request_to_dict(request))


def sink_decision_to_dict(decision: SinkDecision) -> dict[str, Any]:
    """Serialize a stable deterministic decision."""
    return {
        "schema_version": decision.schema_version,
        "status": decision.status.value,
        "reason": decision.reason.value,
        "request_sha256": decision.request_sha256,
        "requires_authority_approval": decision.requires_authority_approval,
        "decision_sha256": decision.decision_sha256,
    }


def sink_decision_from_dict(data: Mapping[str, Any]) -> SinkDecision:
    """Parse and verify one decision digest."""
    _require_fields(
        data,
        {
            "schema_version",
            "status",
            "reason",
            "request_sha256",
            "requires_authority_approval",
            "decision_sha256",
        },
        field_name="sink decision",
    )
    decision = SinkDecision(
        schema_version=_required_int(
            data["schema_version"], field_name="schema_version"
        ),
        status=_required_enum(
            data["status"],
            SinkDecisionStatus,
            field_name="status",
        ),
        reason=_required_enum(
            data["reason"],
            SinkDecisionReason,
            field_name="reason",
        ),
        request_sha256=_required_text(
            data["request_sha256"],
            field_name="request_sha256",
        ),
        requires_authority_approval=_required_bool(
            data["requires_authority_approval"],
            field_name="requires_authority_approval",
        ),
    )
    if data["decision_sha256"] != decision.decision_sha256:
        raise ValueError("sink decision digest does not match")
    return decision


def data_flow_receipt_to_dict(receipt: DataFlowReceipt) -> dict[str, Any]:
    """Serialize an immutable receipt without raw content or credentials."""
    return {
        "schema_version": receipt.schema_version,
        "receipt_id": receipt.receipt_id,
        "request_sha256": receipt.request_sha256,
        "sink_kind": receipt.sink_kind.value,
        "destination_metadata": receipt.destination_metadata,
        "destination_sha256": receipt.destination_sha256,
        "payload_sha256": receipt.payload_sha256,
        "payload_preview": receipt.payload_preview,
        "sources": [source_ref_to_dict(source) for source in receipt.sources],
        "decision": sink_decision_to_dict(receipt.decision),
    }


def data_flow_receipt_from_dict(data: Mapping[str, Any]) -> DataFlowReceipt:
    """Parse a strict receipt and verify its deterministic receipt id."""
    _require_fields(
        data,
        {
            "schema_version",
            "receipt_id",
            "request_sha256",
            "sink_kind",
            "destination_metadata",
            "destination_sha256",
            "payload_sha256",
            "payload_preview",
            "sources",
            "decision",
        },
        field_name="data-flow receipt",
    )
    receipt = DataFlowReceipt(
        schema_version=_required_int(
            data["schema_version"], field_name="schema_version"
        ),
        receipt_id=_required_text(data["receipt_id"], field_name="receipt_id"),
        request_sha256=_required_text(
            data["request_sha256"],
            field_name="request_sha256",
        ),
        sink_kind=_required_enum(
            data["sink_kind"],
            SinkKind,
            field_name="sink_kind",
        ),
        destination_metadata=_required_text(
            data["destination_metadata"],
            field_name="destination_metadata",
        ),
        destination_sha256=_required_text(
            data["destination_sha256"],
            field_name="destination_sha256",
        ),
        payload_sha256=_required_text(
            data["payload_sha256"],
            field_name="payload_sha256",
        ),
        payload_preview=_required_string(
            data["payload_preview"],
            field_name="payload_preview",
        ),
        sources=tuple(
            source_ref_from_dict(_required_mapping(item, field_name="source"))
            for item in _required_list(data["sources"], field_name="sources")
        ),
        decision=sink_decision_from_dict(
            _required_mapping(data["decision"], field_name="decision")
        ),
    )
    if receipt.receipt_id != data_flow_receipt_digest(receipt):
        raise ValueError("data-flow receipt id does not match")
    return receipt


def data_flow_receipt_digest(receipt: DataFlowReceipt) -> str:
    """Return a stable receipt id excluding the id field itself."""
    payload = data_flow_receipt_to_dict(receipt)
    payload.pop("receipt_id")
    return _canonical_hash(payload)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_fields(
    data: Mapping[str, Any],
    expected: set[str],
    *,
    field_name: str,
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{field_name} must be an object")
    unknown = sorted(set(data) - expected)
    missing = sorted(expected - set(data))
    if unknown:
        raise ValueError(f"unknown {field_name} fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"missing {field_name} fields: {', '.join(missing)}")


def _required_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _required_list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def _required_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _required_text(value: Any, *, field_name: str) -> str:
    value = _required_string(value, field_name=field_name)
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name=field_name)


def _required_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _required_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _required_enum(value: Any, enum_type: type[Enum], *, field_name: str):
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is invalid")
    try:
        return enum_type(value)
    except ValueError:
        raise ValueError(f"{field_name} is invalid") from None


def _text_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    return tuple(
        _required_text(item, field_name=field_name)
        for item in _required_list(value, field_name=field_name)
    )


__all__ = [
    "data_flow_receipt_digest",
    "data_flow_receipt_from_dict",
    "data_flow_receipt_to_dict",
    "sink_decision_from_dict",
    "sink_decision_to_dict",
    "sink_request_digest",
    "sink_request_from_dict",
    "sink_request_to_dict",
    "source_ref_from_dict",
    "source_ref_to_dict",
]
