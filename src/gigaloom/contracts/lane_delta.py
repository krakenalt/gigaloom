"""Content-free lane-delta continuation packet contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, cast, runtime_checkable

from gigaloom.contracts.operational_evidence import OperationalEvidenceV1
from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    canonical_digest,
    normalize_digests,
    normalize_identities,
    validate_digest,
    validate_identity,
    validate_schema_version,
    validate_timestamp,
)


LANE_DELTA_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION


class AttachmentBindingStatus(str, Enum):
    """Explicit attachment transfer/verification state."""

    AVAILABLE = "attachment_available"
    MISSING = "attachment_missing"
    NOT_TRANSFERRED = "attachment_not_transferred"
    DIGEST_MISMATCH = "attachment_digest_mismatch"


class LaneDisclosureMode(str, Enum):
    """Continuation disclosure available to the destination lane."""

    NATIVE_RESUME = "native_resume"
    PACKET = "packet"
    REPLAY = "replay"
    FRESH = "fresh"
    DEGRADED = "degraded"


class LaneContentMode(str, Enum):
    """Explicit content admission independent of continuation disclosure."""

    CONTENT_FREE = "content_free"
    EXPLICIT_CONTENT = "explicit_content"


@dataclass(frozen=True, slots=True)
class LaneIdentityV1:
    """Mechanical agent/route/model/account/session lane identity."""

    agent_id: str
    route_id: str
    model_id: str
    account_ref: str
    workspace_fingerprint: str
    policy_digest: str
    context_manifest_digest: str
    run_capsule_digest: str
    session_id: str | None
    schema_version: int = LANE_DELTA_SCHEMA_VERSION
    lane_digest: str = field(init=False)

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="lane identity")
        for value, label in (
            (self.agent_id, "lane agent id"),
            (self.route_id, "lane route id"),
            (self.model_id, "lane model id"),
            (self.account_ref, "lane account reference"),
        ):
            validate_identity(value, field_name=label)
        if self.session_id is not None:
            validate_identity(self.session_id, field_name="lane session id")
        for value, label in (
            (self.workspace_fingerprint, "lane workspace fingerprint"),
            (self.policy_digest, "lane policy digest"),
            (self.context_manifest_digest, "lane context manifest digest"),
            (self.run_capsule_digest, "lane run capsule digest"),
        ):
            validate_digest(value, field_name=label)
        object.__setattr__(
            self,
            "lane_digest",
            canonical_digest(
                {
                    "schema_version": self.schema_version,
                    "agent_id": self.agent_id,
                    "route_id": self.route_id,
                    "model_id": self.model_id,
                    "account_ref": self.account_ref,
                    "workspace_fingerprint": self.workspace_fingerprint,
                    "policy_digest": self.policy_digest,
                    "context_manifest_digest": self.context_manifest_digest,
                    "run_capsule_digest": self.run_capsule_digest,
                    "session_id": self.session_id,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class LaneTurnRangeV1:
    """Contiguous missed turn sequence range."""

    first_sequence: int
    last_sequence: int
    schema_version: int = LANE_DELTA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="lane turn range")
        for value, label in (
            (self.first_sequence, "lane first sequence"),
            (self.last_sequence, "lane last sequence"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} is invalid")
        if self.last_sequence < self.first_sequence:
            raise ValueError("lane missed turn range is reversed")


@dataclass(frozen=True, slots=True)
class LaneAttachmentBindingV1:
    """Content-free attachment availability and digest binding."""

    attachment_id: str
    expected_digest: str
    observed_digest: str | None
    status: AttachmentBindingStatus
    schema_version: int = LANE_DELTA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="lane attachment")
        validate_identity(self.attachment_id, field_name="lane attachment id")
        validate_digest(
            self.expected_digest,
            field_name="lane attachment expected digest",
        )
        if self.observed_digest is not None:
            validate_digest(
                self.observed_digest,
                field_name="lane attachment observed digest",
            )
        if not isinstance(self.status, AttachmentBindingStatus):
            raise ValueError("lane attachment status is invalid")
        if self.status is AttachmentBindingStatus.AVAILABLE:
            if self.observed_digest != self.expected_digest:
                raise ValueError("available attachment requires matching digest")
        elif self.status is AttachmentBindingStatus.DIGEST_MISMATCH:
            if (
                self.observed_digest is None
                or self.observed_digest == self.expected_digest
            ):
                raise ValueError("digest mismatch requires a distinct observed digest")
        elif self.observed_digest is not None:
            raise ValueError(
                "missing or untransferred attachment has no observed digest"
            )


@dataclass(frozen=True, slots=True)
class LaneTruncationV1:
    """Explicit bounded-delta truncation evidence."""

    truncated: bool
    original_count: int
    included_count: int
    reason_code: str | None
    schema_version: int = LANE_DELTA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="lane truncation")
        if not isinstance(self.truncated, bool):
            raise ValueError("lane truncation flag must be boolean")
        for value, label in (
            (self.original_count, "lane original count"),
            (self.included_count, "lane included count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} is invalid")
        if self.included_count > self.original_count:
            raise ValueError("lane included count exceeds original count")
        if self.truncated:
            if self.included_count >= self.original_count or self.reason_code is None:
                raise ValueError("truncated lane delta requires loss and a reason")
            validate_identity(self.reason_code, field_name="lane truncation reason")
        elif self.included_count != self.original_count or self.reason_code is not None:
            raise ValueError("untruncated lane delta cannot claim loss or a reason")


@dataclass(frozen=True, slots=True)
class LaneDeltaPacketV1:
    """Bounded continuation evidence emitted only for a lane change."""

    packet_id: str
    source_lane: LaneIdentityV1
    destination_lane: LaneIdentityV1
    last_shared_turn: str
    missed_turn_range: LaneTurnRangeV1
    context_manifest_digests: tuple[str, ...]
    run_capsule_digests: tuple[str, ...]
    changed_anchors: tuple[OperationalEvidenceV1, ...]
    attachment_bindings: tuple[LaneAttachmentBindingV1, ...]
    disclosure_mode: LaneDisclosureMode
    truncation: LaneTruncationV1
    omissions: tuple[str, ...]
    created_at: datetime
    content_mode: LaneContentMode = LaneContentMode.CONTENT_FREE
    schema_version: int = LANE_DELTA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="lane delta packet")
        validate_identity(self.packet_id, field_name="lane delta packet id")
        if not isinstance(self.source_lane, LaneIdentityV1) or not isinstance(
            self.destination_lane,
            LaneIdentityV1,
        ):
            raise ValueError("lane delta identities are invalid")
        if self.source_lane.lane_digest == self.destination_lane.lane_digest:
            raise ValueError("unchanged lane cannot produce a delta packet")
        validate_identity(self.last_shared_turn, field_name="lane last shared turn")
        if not isinstance(self.missed_turn_range, LaneTurnRangeV1):
            raise ValueError("lane missed turn range is invalid")
        object.__setattr__(
            self,
            "context_manifest_digests",
            normalize_digests(
                self.context_manifest_digests,
                field_name="lane context manifest digests",
            ),
        )
        if set(self.context_manifest_digests) != {
            self.source_lane.context_manifest_digest,
            self.destination_lane.context_manifest_digest,
        }:
            raise ValueError("lane context manifests do not match lane identities")
        object.__setattr__(
            self,
            "run_capsule_digests",
            normalize_digests(
                self.run_capsule_digests,
                field_name="lane run capsule digests",
            ),
        )
        if set(self.run_capsule_digests) != {
            self.source_lane.run_capsule_digest,
            self.destination_lane.run_capsule_digest,
        }:
            raise ValueError("lane run capsules do not match lane identities")
        anchors = _normalize_anchors(self.changed_anchors)
        object.__setattr__(self, "changed_anchors", anchors)
        bindings = _normalize_bindings(self.attachment_bindings)
        object.__setattr__(self, "attachment_bindings", bindings)
        if not isinstance(self.disclosure_mode, LaneDisclosureMode):
            raise ValueError("lane disclosure mode is invalid")
        if not isinstance(self.content_mode, LaneContentMode):
            raise ValueError("lane content mode is invalid")
        if not isinstance(self.truncation, LaneTruncationV1):
            raise ValueError("lane truncation evidence is invalid")
        object.__setattr__(
            self,
            "omissions",
            normalize_identities(
                self.omissions,
                field_name="lane delta omissions",
            ),
        )
        validate_timestamp(self.created_at, field_name="lane delta created_at")


@runtime_checkable
class LaneDeltaPacketPort(Protocol):
    """Public persistence boundary for verified lane-delta packets."""

    def save(self, packet: LaneDeltaPacketV1) -> None:
        """Persist one validated bounded lane-delta packet."""


def _normalize_anchors(
    values: object,
) -> tuple[OperationalEvidenceV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > 256
        or any(not isinstance(item, OperationalEvidenceV1) for item in values)
    ):
        raise ValueError("lane changed anchors must be a bounded evidence tuple")
    typed = cast(tuple[OperationalEvidenceV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.evidence_id))
    ids = [item.evidence_id for item in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("lane changed anchor ids must be unique")
    return normalized


def _normalize_bindings(
    values: object,
) -> tuple[LaneAttachmentBindingV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > 128
        or any(not isinstance(item, LaneAttachmentBindingV1) for item in values)
    ):
        raise ValueError("lane attachment bindings must be a bounded tuple")
    typed = cast(tuple[LaneAttachmentBindingV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.attachment_id))
    ids = [item.attachment_id for item in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("lane attachment binding ids must be unique")
    return normalized
