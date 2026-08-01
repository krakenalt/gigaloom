"""Atomic bounded persistence for verified lane-delta packets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from gigaloom.contracts import (
    LaneContentMode,
    LaneDeltaPacketV1,
    LaneIdentityV1,
    lane_delta_packet_from_dict,
    lane_delta_packet_to_dict,
)
from gigaloom.contracts.operational_validation import (
    canonical_json_bytes,
    validate_digest,
    validate_identity,
)


LANE_DELTA_RECORD_KIND = "gigaloom.lane_delta_packet.v1"
MAX_LANE_DELTA_PACKET_BYTES = 128 * 1024
MAX_EXPLICIT_CONTENT_PACKET_BYTES = 16 * 1024


class LaneDeltaStorageError(RuntimeError):
    """Raised when a lane-delta packet cannot be persisted or loaded."""


class LaneDeltaIntegrityError(LaneDeltaStorageError):
    """Raised when stored bytes or lane bindings fail verification."""


class StaleLaneSourceError(LaneDeltaIntegrityError):
    """Raised when the source lane changed before persistence."""


class LaneDeltaConflictError(LaneDeltaStorageError):
    """Raised when one packet id is already bound to different bytes."""


@dataclass(frozen=True, slots=True)
class StoredLaneDeltaPacketV1:
    """Verified storage receipt without an absolute filesystem path."""

    packet: LaneDeltaPacketV1
    packet_sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_digest(self.packet_sha256, field_name="stored lane packet digest")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes <= 0
            or self.size_bytes > MAX_LANE_DELTA_PACKET_BYTES
        ):
            raise LaneDeltaIntegrityError("stored lane packet size is invalid")


class FilesystemLaneDeltaPacketStore:
    """Persist immutable packets at one digest-addressed path per packet id."""

    def __init__(
        self,
        data_dir: str | os.PathLike[str],
        *,
        allow_explicit_content: bool = False,
    ) -> None:
        self.root = Path(data_dir).expanduser() / "review" / "lane-deltas-v1"
        self.allow_explicit_content = allow_explicit_content

    def persist(
        self,
        packet: LaneDeltaPacketV1,
        *,
        current_source_lane: LaneIdentityV1,
        current_destination_lane: LaneIdentityV1,
    ) -> StoredLaneDeltaPacketV1:
        """Atomically persist a packet after revalidating both current lanes."""
        _verify_current_lanes(
            packet,
            current_source_lane=current_source_lane,
            current_destination_lane=current_destination_lane,
        )
        data, packet_sha256 = self._encode(packet)
        path = self._path(packet.packet_id)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._verify_existing(path, data, packet.content_mode)
            else:
                try:
                    _atomic_create(path, data)
                except FileExistsError:
                    self._verify_existing(path, data, packet.content_mode)
        except LaneDeltaStorageError:
            raise
        except OSError as error:
            raise LaneDeltaStorageError("lane packet could not be persisted") from error
        return StoredLaneDeltaPacketV1(
            packet=packet,
            packet_sha256=packet_sha256,
            size_bytes=len(data),
        )

    def load(
        self,
        packet_id: str,
        *,
        expected_source_lane: LaneIdentityV1,
        expected_destination_lane: LaneIdentityV1,
    ) -> StoredLaneDeltaPacketV1:
        """Load one exact packet and verify bytes plus both expected lanes."""
        path = self._path(packet_id)
        try:
            data = _read_bounded(path, MAX_LANE_DELTA_PACKET_BYTES)
        except FileNotFoundError as error:
            raise KeyError(packet_id) from error
        except OSError as error:
            raise LaneDeltaStorageError("lane packet could not be read") from error
        packet, packet_sha256 = _decode_record(data)
        if packet.packet_id != packet_id:
            raise LaneDeltaIntegrityError("lane packet lookup binding does not match")
        _verify_expected_lanes(
            packet,
            expected_source_lane=expected_source_lane,
            expected_destination_lane=expected_destination_lane,
        )
        self._admit_content_mode(packet.content_mode, len(data))
        return StoredLaneDeltaPacketV1(
            packet=packet,
            packet_sha256=packet_sha256,
            size_bytes=len(data),
        )

    def _encode(self, packet: LaneDeltaPacketV1) -> tuple[bytes, str]:
        if not isinstance(packet, LaneDeltaPacketV1):
            raise LaneDeltaIntegrityError("lane packet is invalid")
        packet_payload = lane_delta_packet_to_dict(packet)
        packet_sha256 = hashlib.sha256(canonical_json_bytes(packet_payload)).hexdigest()
        data = canonical_json_bytes(
            {
                "kind": LANE_DELTA_RECORD_KIND,
                "packet": packet_payload,
                "packet_sha256": packet_sha256,
                "source_lane_digest": packet.source_lane.lane_digest,
                "destination_lane_digest": packet.destination_lane.lane_digest,
            }
        )
        self._admit_content_mode(packet.content_mode, len(data))
        return data, packet_sha256

    def _admit_content_mode(self, mode: LaneContentMode, size_bytes: int) -> None:
        if mode is LaneContentMode.EXPLICIT_CONTENT:
            if not self.allow_explicit_content:
                raise LaneDeltaStorageError(
                    "explicit-content lane packets require store opt-in"
                )
            if size_bytes > MAX_EXPLICIT_CONTENT_PACKET_BYTES:
                raise LaneDeltaStorageError(
                    "explicit-content lane packet exceeds its independent limit"
                )
        elif size_bytes > MAX_LANE_DELTA_PACKET_BYTES:
            raise LaneDeltaStorageError("lane packet exceeds the storage limit")

    def _size_limit(self, mode: LaneContentMode) -> int:
        if mode is LaneContentMode.EXPLICIT_CONTENT:
            return MAX_EXPLICIT_CONTENT_PACKET_BYTES
        return MAX_LANE_DELTA_PACKET_BYTES

    def _verify_existing(
        self,
        path: Path,
        data: bytes,
        mode: LaneContentMode,
    ) -> None:
        existing = _read_bounded(path, self._size_limit(mode))
        if existing != data:
            raise LaneDeltaConflictError(
                "lane packet id is already bound to different bytes"
            )

    def _path(self, packet_id: str) -> Path:
        validate_identity(packet_id, field_name="lane packet id")
        name = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()
        return self.root / f"{name}.json"


def _verify_current_lanes(
    packet: LaneDeltaPacketV1,
    *,
    current_source_lane: LaneIdentityV1,
    current_destination_lane: LaneIdentityV1,
) -> None:
    if not isinstance(packet, LaneDeltaPacketV1):
        raise LaneDeltaIntegrityError("lane packet is invalid")
    if not isinstance(current_source_lane, LaneIdentityV1) or not isinstance(
        current_destination_lane,
        LaneIdentityV1,
    ):
        raise LaneDeltaIntegrityError("current lane identities are invalid")
    if current_source_lane.lane_digest != packet.source_lane.lane_digest:
        raise StaleLaneSourceError("lane packet source identity is stale")
    if current_destination_lane.lane_digest != packet.destination_lane.lane_digest:
        raise LaneDeltaIntegrityError("lane packet destination identity changed")


def _verify_expected_lanes(
    packet: LaneDeltaPacketV1,
    *,
    expected_source_lane: LaneIdentityV1,
    expected_destination_lane: LaneIdentityV1,
) -> None:
    if packet.source_lane.lane_digest != expected_source_lane.lane_digest:
        raise StaleLaneSourceError("stored lane packet source digest does not match")
    if packet.destination_lane.lane_digest != expected_destination_lane.lane_digest:
        raise LaneDeltaIntegrityError(
            "stored lane packet destination digest does not match"
        )


def _decode_record(data: bytes) -> tuple[LaneDeltaPacketV1, str]:
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LaneDeltaIntegrityError("lane packet is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != {
        "kind",
        "packet",
        "packet_sha256",
        "source_lane_digest",
        "destination_lane_digest",
    }:
        raise LaneDeltaIntegrityError("lane packet record fields are invalid")
    if canonical_json_bytes(payload) != data:
        raise LaneDeltaIntegrityError("lane packet record is not canonical JSON")
    if payload["kind"] != LANE_DELTA_RECORD_KIND:
        raise LaneDeltaIntegrityError("lane packet record kind is invalid")
    packet_payload = payload["packet"]
    if not isinstance(packet_payload, dict):
        raise LaneDeltaIntegrityError("lane packet payload is invalid")
    packet_sha256 = hashlib.sha256(canonical_json_bytes(packet_payload)).hexdigest()
    if payload["packet_sha256"] != packet_sha256:
        raise LaneDeltaIntegrityError("lane packet digest does not match")
    try:
        packet = lane_delta_packet_from_dict(packet_payload)
    except ValueError as error:
        raise LaneDeltaIntegrityError("lane packet contract is invalid") from error
    if payload["source_lane_digest"] != packet.source_lane.lane_digest:
        raise LaneDeltaIntegrityError("lane packet source envelope does not match")
    if payload["destination_lane_digest"] != packet.destination_lane.lane_digest:
        raise LaneDeltaIntegrityError("lane packet destination envelope does not match")
    return packet, packet_sha256


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LaneDeltaIntegrityError("lane packet contains a duplicate key")
        value[key] = item
    return value


def _read_bounded(path: Path, limit: int) -> bytes:
    if path.is_symlink():
        raise LaneDeltaIntegrityError("lane packet path cannot be a symlink")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise LaneDeltaIntegrityError("lane packet exceeds the storage limit")
    return data


def _atomic_create(path: Path, data: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
