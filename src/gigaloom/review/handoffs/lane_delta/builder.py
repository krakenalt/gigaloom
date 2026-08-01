"""Bounded mechanical lane-delta construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from gigaloom.contracts import (
    AttachmentBindingStatus,
    LaneAttachmentBindingV1,
    LaneContentMode,
    LaneDeltaPacketV1,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTruncationV1,
    LaneTurnRangeV1,
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
)
from gigaloom.contracts.operational_validation import (
    canonical_digest,
    normalize_identities,
    validate_digest,
    validate_identity,
)


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
    "provider_hidden_state",
    "raw_content",
    "secret_material",
)
MAX_LANE_ATTACHMENTS = 128
_MAX_DECLARED_OMISSIONS = 122


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
    attachment_observations: tuple[LaneAttachmentObservationV1, ...] | None = None
    declared_omissions: tuple[str, ...] = ()
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
        observations = self.attachment_observations
        if observations is not None:
            if (
                not isinstance(observations, tuple)
                or len(observations) > MAX_LANE_ATTACHMENTS
                or any(
                    not isinstance(item, LaneAttachmentObservationV1)
                    for item in observations
                )
            ):
                raise LaneDeltaBuildError(
                    "attachment observations must be a bounded tuple"
                )
            normalized = tuple(
                sorted(observations, key=lambda item: item.attachment_id)
            )
            if len({item.attachment_id for item in normalized}) != len(normalized):
                raise LaneDeltaBuildError("attachment observation ids must be unique")
            object.__setattr__(self, "attachment_observations", normalized)
        try:
            omissions = normalize_identities(
                self.declared_omissions,
                field_name="declared lane omissions",
                maximum=_MAX_DECLARED_OMISSIONS,
            )
        except ValueError as error:
            raise LaneDeltaBuildError(str(error)) from error
        object.__setattr__(self, "declared_omissions", omissions)


@dataclass(frozen=True, slots=True)
class LaneAttachmentObservationV1:
    """Content-free facts supplied by the attachment owner."""

    attachment_id: str
    expected_digest: str
    source_available: bool
    transfer_requested: bool
    destination_digest: str | None

    def __post_init__(self) -> None:
        validate_identity(self.attachment_id, field_name="lane attachment id")
        validate_digest(
            self.expected_digest,
            field_name="lane attachment expected digest",
        )
        if not isinstance(self.source_available, bool) or not isinstance(
            self.transfer_requested,
            bool,
        ):
            raise LaneDeltaBuildError("attachment availability flags must be boolean")
        if self.destination_digest is not None:
            validate_digest(
                self.destination_digest,
                field_name="lane attachment destination digest",
            )
        if not self.source_available and self.destination_digest is not None:
            raise LaneDeltaBuildError(
                "unavailable source attachment cannot have a destination digest"
            )
        if not self.transfer_requested and self.destination_digest is not None:
            raise LaneDeltaBuildError(
                "untransferred attachment cannot have a destination digest"
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
        bindings, attachment_omissions = _bind_attachments(
            request.attachment_observations
        )
        omissions = tuple(
            sorted(
                {
                    *_CONTENT_FREE_OMISSIONS,
                    *request.declared_omissions,
                    *attachment_omissions,
                }
            )
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
                "attachment_bindings": [
                    {
                        "attachment_id": item.attachment_id,
                        "expected_digest": item.expected_digest,
                        "observed_digest": item.observed_digest,
                        "status": item.status.value,
                    }
                    for item in bindings
                ],
                "omissions": list(omissions),
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
            attachment_bindings=bindings,
            disclosure_mode=request.disclosure_mode,
            truncation=LaneTruncationV1(
                truncated=False,
                original_count=len(anchors),
                included_count=len(anchors),
                reason_code=None,
            ),
            omissions=omissions,
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


def _bind_attachments(
    observations: tuple[LaneAttachmentObservationV1, ...] | None,
) -> tuple[tuple[LaneAttachmentBindingV1, ...], tuple[str, ...]]:
    if observations is None:
        return (), ("attachment_inventory_not_evaluated",)

    bindings: list[LaneAttachmentBindingV1] = []
    omissions: set[str] = set()
    for observation in observations:
        status = _attachment_status(observation)
        if status is not AttachmentBindingStatus.AVAILABLE:
            omissions.add(status.value)
        bindings.append(
            LaneAttachmentBindingV1(
                attachment_id=observation.attachment_id,
                expected_digest=observation.expected_digest,
                observed_digest=observation.destination_digest,
                status=status,
            )
        )
    return tuple(bindings), tuple(sorted(omissions))


def _attachment_status(
    observation: LaneAttachmentObservationV1,
) -> AttachmentBindingStatus:
    if not observation.source_available:
        return AttachmentBindingStatus.MISSING
    if not observation.transfer_requested:
        return AttachmentBindingStatus.NOT_TRANSFERRED
    if observation.destination_digest is None:
        return AttachmentBindingStatus.MISSING
    if observation.destination_digest != observation.expected_digest:
        return AttachmentBindingStatus.DIGEST_MISMATCH
    return AttachmentBindingStatus.AVAILABLE
