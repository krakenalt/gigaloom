"""Bounded mechanical lane-delta construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from gigaloom.contracts import (
    LaneContentMode,
    LaneDeltaPacketV1,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTruncationV1,
    LaneTurnRangeV1,
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
)
from gigaloom.contracts.operational_validation import canonical_digest


_LANE_IDENTITY_FIELDS = (
    "agent_id",
    "route_id",
    "model_id",
    "account_ref",
    "session_id",
    "workspace_fingerprint",
    "policy_digest",
    "context_manifest_digest",
    "run_capsule_digest",
)
LANE_IDENTITY_FIELD_COUNT = len(_LANE_IDENTITY_FIELDS)
_CONTENT_FREE_OMISSIONS = (
    "attachment_inventory_not_evaluated",
    "provider_hidden_state",
    "raw_content",
    "secret_material",
)


class LaneDeltaBuildError(ValueError):
    """Raised when a truthful bounded lane delta cannot be built."""


@dataclass(frozen=True, slots=True)
class LaneDeltaBuildRequestV1:
    """Inputs already resolved by the source and destination lane owners."""

    source_lane: LaneIdentityV1
    destination_lane: LaneIdentityV1
    last_shared_turn: str
    missed_turn_range: LaneTurnRangeV1
    disclosure_mode: LaneDisclosureMode
    created_at: datetime
    content_mode: LaneContentMode = LaneContentMode.CONTENT_FREE

    def __post_init__(self) -> None:
        if not isinstance(self.source_lane, LaneIdentityV1) or not isinstance(
            self.destination_lane,
            LaneIdentityV1,
        ):
            raise LaneDeltaBuildError("lane identities are invalid")
        if not isinstance(self.missed_turn_range, LaneTurnRangeV1):
            raise LaneDeltaBuildError("missed turn range is invalid")
        if not isinstance(self.disclosure_mode, LaneDisclosureMode):
            raise LaneDeltaBuildError("disclosure mode is invalid")
        if self.content_mode is not LaneContentMode.CONTENT_FREE:
            raise LaneDeltaBuildError(
                "mechanical lane deltas are content-free unless a separate "
                "content admission is provided"
            )


class LaneDeltaBuilder:
    """Create a content-free packet from a constant-size lane comparison."""

    def build(self, request: LaneDeltaBuildRequestV1) -> LaneDeltaPacketV1 | None:
        """Return no packet for an unchanged lane, otherwise one exact delta."""
        if not isinstance(request, LaneDeltaBuildRequestV1):
            raise LaneDeltaBuildError("lane delta request is invalid")
        if request.source_lane.lane_digest == request.destination_lane.lane_digest:
            return None

        anchors = tuple(
            _changed_anchor(field_name, source_value, destination_value)
            for field_name in _LANE_IDENTITY_FIELDS
            if (source_value := getattr(request.source_lane, field_name))
            != (destination_value := getattr(request.destination_lane, field_name))
        )
        packet_digest = canonical_digest(
            {
                "source_lane_digest": request.source_lane.lane_digest,
                "destination_lane_digest": request.destination_lane.lane_digest,
                "last_shared_turn": request.last_shared_turn,
                "missed_turn_first": request.missed_turn_range.first_sequence,
                "missed_turn_last": request.missed_turn_range.last_sequence,
                "disclosure_mode": request.disclosure_mode.value,
                "anchor_digests": [item.evidence_digest for item in anchors],
                "created_at": request.created_at.isoformat(),
            }
        )
        return LaneDeltaPacketV1(
            packet_id=f"lane-delta-{packet_digest[:32]}",
            source_lane=request.source_lane,
            destination_lane=request.destination_lane,
            last_shared_turn=request.last_shared_turn,
            missed_turn_range=request.missed_turn_range,
            context_manifest_digests=_distinct_digests(
                request.source_lane.context_manifest_digest,
                request.destination_lane.context_manifest_digest,
            ),
            run_capsule_digests=_distinct_digests(
                request.source_lane.run_capsule_digest,
                request.destination_lane.run_capsule_digest,
            ),
            changed_anchors=anchors,
            attachment_bindings=(),
            disclosure_mode=request.disclosure_mode,
            truncation=LaneTruncationV1(
                truncated=False,
                original_count=len(anchors),
                included_count=len(anchors),
                reason_code=None,
            ),
            omissions=_CONTENT_FREE_OMISSIONS,
            created_at=request.created_at,
        )


def _changed_anchor(
    field_name: str,
    source_value: str | None,
    destination_value: str | None,
) -> OperationalEvidenceV1:
    anchor_digest = canonical_digest(
        {
            "field": field_name,
            "source": source_value,
            "destination": destination_value,
        }
    )
    return OperationalEvidenceV1(
        evidence_id=f"lane-anchor-{field_name.replace('_', '-')}",
        kind="lane_identity_delta",
        status=OperationalEvidenceStatus.WARNING,
        evidence_digest=anchor_digest,
        reason_code=f"{field_name}_changed",
        source_digest=canonical_digest({"source": source_value}),
    )


def _distinct_digests(source: str, destination: str) -> tuple[str, ...]:
    return tuple(sorted({source, destination}))
