"""Contract tests for bounded source-to-sink evidence."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from gigaloom.contracts import (
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
    data_flow_receipt_digest,
    data_flow_receipt_from_dict,
    data_flow_receipt_to_dict,
    sink_request_digest,
    sink_request_from_dict,
    sink_request_to_dict,
    source_ref_from_dict,
    source_ref_to_dict,
)


def test_source_and_sink_request_round_trip_with_stable_digest() -> None:
    request = _request((_source("user:payload"), _source("user:destination")))
    payload = sink_request_to_dict(request)

    assert sink_request_from_dict(payload) == request
    assert sink_request_digest(sink_request_from_dict(payload)) == (
        sink_request_digest(request)
    )
    assert [item["source_id"] for item in payload["influence"]] == [
        "user:destination",
        "user:payload",
    ]
    assert "send this payload" in payload["payload_preview"]


def test_receipt_round_trip_verifies_receipt_and_decision_digests() -> None:
    request = _request((_source("user:destination"), _source("user:payload")))
    request_sha256 = sink_request_digest(request)
    decision = SinkDecision(
        status=SinkDecisionStatus.ALLOW,
        reason=SinkDecisionReason.ADMITTED,
        request_sha256=request_sha256,
        requires_authority_approval=True,
    )
    draft = DataFlowReceipt(
        receipt_id="0" * 64,
        request_sha256=request_sha256,
        sink_kind=request.sink_kind,
        destination_metadata=request.destination_metadata,
        destination_sha256=request.destination_sha256,
        payload_sha256=request.payload_sha256,
        payload_preview=request.payload_preview,
        sources=request.influence.sources,
        decision=decision,
    )
    receipt = replace(draft, receipt_id=data_flow_receipt_digest(draft))
    payload = data_flow_receipt_to_dict(receipt)

    assert data_flow_receipt_from_dict(payload) == receipt
    payload["decision"]["decision_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="decision digest"):
        data_flow_receipt_from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", 2),
        ("provenance", "future_source"),
        ("trust", "assumed_safe"),
        ("sensitivity", "classified"),
    ),
)
def test_unknown_source_schema_or_enum_fails_closed(
    field: str,
    value: object,
) -> None:
    payload = source_ref_to_dict(_source("user:payload"))
    payload[field] = value

    with pytest.raises(ValueError):
        source_ref_from_dict(payload)


def test_unknown_legacy_provenance_cannot_be_assigned_known_trust() -> None:
    with pytest.raises(ValueError, match="unknown provenance"):
        SourceRef(
            source_id="legacy:record",
            provenance=None,
            trust=TrustClass.BOUNDED,
            sensitivity=Sensitivity.INTERNAL,
            content_sha256=_digest("legacy content"),
        )

    unknown = SourceRef(
        source_id="legacy:record",
        provenance=None,
        trust=TrustClass.UNKNOWN,
        sensitivity=Sensitivity.INTERNAL,
        content_sha256=_digest("legacy content"),
    )
    assert source_ref_from_dict(source_ref_to_dict(unknown)) == unknown


@pytest.mark.parametrize(
    "provenance",
    (ProvenanceClass.TERMINAL, ProvenanceClass.GENERATED),
)
def test_terminal_and_generated_influence_cannot_be_promoted_to_trusted(
    provenance: ProvenanceClass,
) -> None:
    with pytest.raises(ValueError, match="must be untrusted"):
        _source("unsafe:authority", provenance=provenance)


def test_secret_request_cannot_retain_payload_preview() -> None:
    with pytest.raises(ValueError, match="cannot retain a preview"):
        replace(
            _request((_source("user:payload"),)),
            payload_sensitivity=Sensitivity.SECRET,
        )


def test_influence_set_rejects_duplicates_and_is_bounded() -> None:
    source = _source("user:payload")
    with pytest.raises(ValueError, match="duplicate"):
        InfluenceSet((source, source))

    with pytest.raises(ValueError, match="source limit"):
        InfluenceSet(tuple(_source(f"user:{index}") for index in range(33)))


def _request(sources: tuple[SourceRef, ...]) -> SinkRequest:
    source_ids = tuple(source.source_id for source in sources)
    payload_sha256 = _digest("send this payload")
    destination_sha256 = _digest("https://example.test/api")
    return SinkRequest(
        request_id="request:one",
        sink_kind=SinkKind.NETWORK_URL,
        destination_metadata="https://example.test/api",
        destination_sha256=destination_sha256,
        approved_destination_sha256=destination_sha256,
        payload_sha256=payload_sha256,
        approved_payload_sha256=payload_sha256,
        payload_preview="send this payload",
        payload_sensitivity=Sensitivity.INTERNAL,
        metadata_sensitivity=Sensitivity.PUBLIC,
        influence=InfluenceSet(sources),
        destination_source_ids=source_ids,
        payload_source_ids=source_ids,
    )


def _source(
    source_id: str,
    *,
    provenance: ProvenanceClass = ProvenanceClass.USER,
) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        provenance=provenance,
        trust=TrustClass.TRUSTED,
        sensitivity=Sensitivity.INTERNAL,
        content_sha256=_digest(source_id),
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
