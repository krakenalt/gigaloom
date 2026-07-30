"""Pure deterministic admission for protected source-to-sink requests."""

from __future__ import annotations

from dataclasses import replace

from gigaloom.contracts import (
    DataFlowReceipt,
    ProvenanceClass,
    Sensitivity,
    SinkDecision,
    SinkDecisionReason,
    SinkDecisionStatus,
    SinkRequest,
    TrustClass,
    data_flow_receipt_digest,
    sink_request_digest,
)


class SourceToSinkGuard:
    """Evaluate bounded influence without model, storage, or network calls."""

    def admit(self, request: SinkRequest) -> tuple[SinkDecision, DataFlowReceipt]:
        """Return an immutable decision and content-free receipt."""
        request_sha256 = sink_request_digest(request)
        reason = _denial_reason(request)
        allowed = reason is None
        decision = SinkDecision(
            status=(SinkDecisionStatus.ALLOW if allowed else SinkDecisionStatus.DENY),
            reason=reason or SinkDecisionReason.ADMITTED,
            request_sha256=request_sha256,
            requires_authority_approval=allowed,
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
        return decision, receipt


def admit_sink_request(
    request: SinkRequest,
) -> tuple[SinkDecision, DataFlowReceipt]:
    """Evaluate one request with the default deterministic policy."""
    return SourceToSinkGuard().admit(request)


def _denial_reason(request: SinkRequest) -> SinkDecisionReason | None:
    if request.redirect_requires_revalidation or (
        request.redirect_destination_sha256 is not None
        and request.redirect_destination_sha256 != request.destination_sha256
    ):
        return SinkDecisionReason.REDIRECT_REVALIDATION_REQUIRED
    if request.destination_sha256 != request.approved_destination_sha256:
        return SinkDecisionReason.DESTINATION_BINDING_MISMATCH
    if request.payload_sha256 != request.approved_payload_sha256:
        return SinkDecisionReason.PAYLOAD_BINDING_MISMATCH
    if request.payload_sensitivity is Sensitivity.SECRET:
        return SinkDecisionReason.SECRET_PAYLOAD
    if request.metadata_sensitivity is Sensitivity.SECRET:
        return SinkDecisionReason.SECRET_METADATA
    if any(
        source.sensitivity is Sensitivity.SECRET for source in request.influence.sources
    ):
        return SinkDecisionReason.SECRET_INFLUENCE
    if any(source.provenance is None for source in request.influence.sources):
        return SinkDecisionReason.UNKNOWN_PROVENANCE
    if any(source.trust is TrustClass.UNKNOWN for source in request.influence.sources):
        return SinkDecisionReason.UNKNOWN_TRUST
    if any(
        source.provenance in {ProvenanceClass.TERMINAL, ProvenanceClass.GENERATED}
        or source.trust in {TrustClass.BOUNDED, TrustClass.UNTRUSTED}
        for source in request.influence.sources
    ):
        return SinkDecisionReason.UNTRUSTED_INFLUENCE
    authored_source_ids = set(request.destination_source_ids) | set(
        request.payload_source_ids
    )
    authored_sources = (
        source
        for source in request.influence.sources
        if source.source_id in authored_source_ids
    )
    if any(
        source.provenance is not ProvenanceClass.USER
        or source.trust is not TrustClass.TRUSTED
        for source in authored_sources
    ):
        return SinkDecisionReason.NON_USER_AUTHORED
    return None


__all__ = ["SourceToSinkGuard", "admit_sink_request"]
