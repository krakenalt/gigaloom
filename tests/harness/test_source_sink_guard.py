"""Deterministic admission tests for protected source-to-sink flows."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from gigaloom.contracts import (
    InfluenceSet,
    ProvenanceClass,
    Sensitivity,
    SinkDecisionReason,
    SinkDecisionStatus,
    SinkKind,
    SinkRequest,
    SourceRef,
    TrustClass,
    data_flow_receipt_to_dict,
)
from gigaloom.execution.api import SourceToSinkGuard, admit_sink_request


def test_exact_user_authored_request_is_admitted_before_authority_approval() -> None:
    request = _request(_user_sources())

    decision, receipt = admit_sink_request(request)

    assert decision.status is SinkDecisionStatus.ALLOW
    assert decision.reason is SinkDecisionReason.ADMITTED
    assert decision.requires_authority_approval is True
    assert receipt.decision == decision
    assert receipt.request_sha256 == decision.request_sha256


@pytest.mark.parametrize(
    ("change", "reason"),
    (
        (
            {"redirect_requires_revalidation": True},
            SinkDecisionReason.REDIRECT_REVALIDATION_REQUIRED,
        ),
        (
            {"approved_destination_sha256": "f" * 64},
            SinkDecisionReason.DESTINATION_BINDING_MISMATCH,
        ),
        (
            {"approved_payload_sha256": "f" * 64},
            SinkDecisionReason.PAYLOAD_BINDING_MISMATCH,
        ),
        (
            {
                "payload_sensitivity": Sensitivity.SECRET,
                "payload_preview": "",
            },
            SinkDecisionReason.SECRET_PAYLOAD,
        ),
        (
            {
                "metadata_sensitivity": Sensitivity.SECRET,
                "payload_preview": "",
            },
            SinkDecisionReason.SECRET_METADATA,
        ),
    ),
)
def test_request_binding_and_sensitivity_fail_closed(
    change: dict[str, object],
    reason: SinkDecisionReason,
) -> None:
    decision, _receipt = admit_sink_request(
        replace(_request(_user_sources()), **change)
    )

    assert decision.status is SinkDecisionStatus.DENY
    assert decision.reason is reason
    assert decision.requires_authority_approval is False


@pytest.mark.parametrize(
    ("source_id", "provenance", "trust", "sensitivity", "reason"),
    (
        (
            "source:secret",
            ProvenanceClass.USER,
            TrustClass.TRUSTED,
            Sensitivity.SECRET,
            SinkDecisionReason.SECRET_INFLUENCE,
        ),
        (
            "source:legacy",
            None,
            TrustClass.UNKNOWN,
            Sensitivity.INTERNAL,
            SinkDecisionReason.UNKNOWN_PROVENANCE,
        ),
        (
            "source:web",
            ProvenanceClass.WEB,
            TrustClass.BOUNDED,
            Sensitivity.INTERNAL,
            SinkDecisionReason.UNTRUSTED_INFLUENCE,
        ),
        (
            "source:terminal",
            ProvenanceClass.TERMINAL,
            TrustClass.UNTRUSTED,
            Sensitivity.INTERNAL,
            SinkDecisionReason.UNTRUSTED_INFLUENCE,
        ),
        (
            "source:repo",
            ProvenanceClass.REPO,
            TrustClass.TRUSTED,
            Sensitivity.INTERNAL,
            SinkDecisionReason.NON_USER_AUTHORED,
        ),
    ),
)
def test_non_user_or_untrusted_influence_cannot_create_sink_authority(
    source_id: str,
    provenance: ProvenanceClass | None,
    trust: TrustClass,
    sensitivity: Sensitivity,
    reason: SinkDecisionReason,
) -> None:
    source = _source(
        source_id,
        provenance=provenance,
        trust=trust,
        sensitivity=sensitivity,
    )
    decision, receipt = admit_sink_request(_request((source,)))

    assert decision.status is SinkDecisionStatus.DENY
    assert decision.reason is reason
    assert receipt.sources == (source,)


def test_duplicate_request_has_identical_decision_and_receipt() -> None:
    request = _request(_user_sources())
    guard = SourceToSinkGuard()

    assert guard.admit(request) == guard.admit(request)


def test_receipt_is_content_free_and_does_not_retain_secret() -> None:
    canary = "sk-live-should-never-be-stored"
    source = _source(
        "source:secret",
        provenance=ProvenanceClass.USER,
        trust=TrustClass.TRUSTED,
        sensitivity=Sensitivity.SECRET,
        content=canary,
    )
    request = replace(
        _request((source,)),
        payload_sensitivity=Sensitivity.SECRET,
        payload_preview="",
        payload_sha256=_digest(canary),
        approved_payload_sha256=_digest(canary),
    )

    decision, receipt = admit_sink_request(request)
    serialized = str(data_flow_receipt_to_dict(receipt))

    assert decision.reason is SinkDecisionReason.SECRET_PAYLOAD
    assert canary not in serialized
    assert receipt.payload_preview == ""
    assert receipt.payload_sha256 == _digest(canary)


def test_every_influence_must_be_bound_to_destination_or_payload() -> None:
    sources = (*_user_sources(), _source("source:unbound"))
    with pytest.raises(ValueError, match="every influence source"):
        replace(
            _request(sources),
            destination_source_ids=("source:destination",),
            payload_source_ids=("source:payload",),
        )


def _request(sources: tuple[SourceRef, ...]) -> SinkRequest:
    source_ids = tuple(source.source_id for source in sources)
    destination_sha256 = _digest("https://example.test/api")
    payload_sha256 = _digest("safe payload")
    return SinkRequest(
        request_id="request:guard",
        sink_kind=SinkKind.NETWORK_URL,
        destination_metadata="https://example.test/api",
        destination_sha256=destination_sha256,
        approved_destination_sha256=destination_sha256,
        payload_sha256=payload_sha256,
        approved_payload_sha256=payload_sha256,
        payload_preview="safe payload",
        payload_sensitivity=Sensitivity.INTERNAL,
        metadata_sensitivity=Sensitivity.PUBLIC,
        influence=InfluenceSet(sources),
        destination_source_ids=source_ids,
        payload_source_ids=source_ids,
    )


def _user_sources() -> tuple[SourceRef, ...]:
    return (
        _source("source:destination"),
        _source("source:payload"),
    )


def _source(
    source_id: str,
    *,
    provenance: ProvenanceClass | None = ProvenanceClass.USER,
    trust: TrustClass = TrustClass.TRUSTED,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    content: str | None = None,
) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        provenance=provenance,
        trust=trust,
        sensitivity=sensitivity,
        content_sha256=_digest(content or source_id),
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
