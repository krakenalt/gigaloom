"""Attachment availability and omission binding contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from gigaloom.contracts import (
    AttachmentBindingStatus,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTurnRangeV1,
)
from gigaloom.review.handoffs.lane_delta import (
    MAX_LANE_ATTACHMENTS,
    LaneAttachmentObservationV1,
    LaneDeltaBuildError,
    LaneDeltaBuildRequestV1,
    LaneDeltaBuilder,
)


NOW = datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _lane(name: str) -> LaneIdentityV1:
    return LaneIdentityV1(
        agent_id=name,
        route_id=f"{name}-route",
        model_id=f"{name}-model",
        account_ref=f"{name}-account",
        session_id=f"{name}-session",
        workspace_fingerprint=_digest(f"{name}-workspace"),
        policy_digest=_digest(f"{name}-policy"),
        context_manifest_digest=_digest(f"{name}-manifest"),
        run_capsule_digest=_digest(f"{name}-capsule"),
    )


def _packet(
    observations: tuple[LaneAttachmentObservationV1, ...] | None,
    *,
    omissions: tuple[str, ...] = (),
):
    return LaneDeltaBuilder().build(
        LaneDeltaBuildRequestV1(
            source_lane=_lane("source"),
            destination_lane=_lane("destination"),
            last_shared_turn="turn-4",
            missed_turn_range=LaneTurnRangeV1(5, 6),
            disclosure_mode=LaneDisclosureMode.PACKET,
            created_at=NOW,
            attachment_observations=observations,
            declared_omissions=omissions,
        )
    )


def _observation(
    attachment_id: str,
    *,
    source_available: bool = True,
    transfer_requested: bool = True,
    destination_digest: str | None = None,
) -> LaneAttachmentObservationV1:
    expected = _digest(attachment_id)
    return LaneAttachmentObservationV1(
        attachment_id=attachment_id,
        expected_digest=expected,
        source_available=source_available,
        transfer_requested=transfer_requested,
        destination_digest=(
            expected if destination_digest == "matching" else destination_digest
        ),
    )


def test_binds_all_attachment_statuses_and_corresponding_omissions() -> None:
    observations = (
        _observation("available", destination_digest="matching"),
        _observation("source-missing", source_available=False),
        _observation("not-transferred", transfer_requested=False),
        _observation("destination-missing"),
        _observation("mismatch", destination_digest=_digest("different")),
    )

    packet = _packet(observations, omissions=("full_session_history",))

    assert packet is not None
    assert {item.attachment_id: item.status for item in packet.attachment_bindings} == {
        "available": AttachmentBindingStatus.AVAILABLE,
        "destination-missing": AttachmentBindingStatus.MISSING,
        "mismatch": AttachmentBindingStatus.DIGEST_MISMATCH,
        "not-transferred": AttachmentBindingStatus.NOT_TRANSFERRED,
        "source-missing": AttachmentBindingStatus.MISSING,
    }
    assert set(packet.omissions) == {
        "attachment_digest_mismatch",
        "attachment_missing",
        "attachment_not_transferred",
        "full_session_history",
        "provider_hidden_state",
        "raw_content",
        "secret_material",
    }


def test_explicit_empty_inventory_does_not_claim_inventory_was_omitted() -> None:
    packet = _packet(())

    assert packet is not None
    assert packet.attachment_bindings == ()
    assert "attachment_inventory_not_evaluated" not in packet.omissions


def test_unset_inventory_is_bound_as_an_omission() -> None:
    packet = _packet(None)

    assert packet is not None
    assert "attachment_inventory_not_evaluated" in packet.omissions


def test_attachment_evidence_changes_packet_identity() -> None:
    without_transfer = _packet((_observation("attachment", transfer_requested=False),))
    with_transfer = _packet(
        (_observation("attachment", destination_digest="matching"),)
    )

    assert without_transfer is not None
    assert with_transfer is not None
    assert without_transfer.packet_id != with_transfer.packet_id


def test_rejects_contradictory_or_duplicate_attachment_observations() -> None:
    with pytest.raises(LaneDeltaBuildError, match="untransferred"):
        _observation(
            "contradictory",
            transfer_requested=False,
            destination_digest="matching",
        )
    duplicate = _observation("duplicate", transfer_requested=False)
    with pytest.raises(LaneDeltaBuildError, match="must be unique"):
        _packet((duplicate, duplicate))


def test_attachment_inventory_has_a_fixed_upper_bound() -> None:
    observations = tuple(
        _observation(f"attachment-{index}", transfer_requested=False)
        for index in range(MAX_LANE_ATTACHMENTS + 1)
    )

    with pytest.raises(LaneDeltaBuildError, match="bounded tuple"):
        _packet(observations)
