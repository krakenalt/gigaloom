"""Atomic lane-delta storage and verification contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Barrier, Thread

import pytest

from gigaloom.contracts import (
    AttachmentBindingStatus,
    LaneAttachmentBindingV1,
    LaneContentMode,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTurnRangeV1,
)
from gigaloom.review.handoffs.lane_delta import (
    FilesystemLaneDeltaPacketStore,
    LaneDeltaBuildRequestV1,
    LaneDeltaBuilder,
    LaneDeltaConflictError,
    LaneDeltaIntegrityError,
    LaneDeltaStorageError,
    StaleLaneSourceError,
)


NOW = datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc)


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


def _packet():
    source = _lane("source")
    destination = _lane("destination")
    packet = LaneDeltaBuilder().build(
        LaneDeltaBuildRequestV1(
            source_lane=source,
            destination_lane=destination,
            last_shared_turn="turn-2",
            missed_turn_range=LaneTurnRangeV1(3, 4),
            disclosure_mode=LaneDisclosureMode.PACKET,
            created_at=NOW,
            attachment_observations=(),
            declared_omissions=("full_session_history",),
        )
    )
    assert packet is not None
    return packet


def _stored_path(root: Path) -> Path:
    paths = list((root / "review" / "lane-deltas-v1").glob("*.json"))
    assert len(paths) == 1
    return paths[0]


def test_persists_canonical_packet_atomically_and_loads_by_exact_lanes(
    tmp_path: Path,
) -> None:
    packet = _packet()
    store = FilesystemLaneDeltaPacketStore(tmp_path)

    first = store.persist(
        packet,
        current_source_lane=packet.source_lane,
        current_destination_lane=packet.destination_lane,
    )
    second = store.persist(
        packet,
        current_source_lane=packet.source_lane,
        current_destination_lane=packet.destination_lane,
    )
    loaded = store.load(
        packet.packet_id,
        expected_source_lane=packet.source_lane,
        expected_destination_lane=packet.destination_lane,
    )

    assert first == second == loaded
    path = _stored_path(tmp_path)
    data = path.read_bytes()
    assert (
        data
        == json.dumps(
            json.loads(data),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )
    assert not list(path.parent.glob("*.tmp"))


def test_rejects_stale_source_and_changed_destination_before_writing(
    tmp_path: Path,
) -> None:
    packet = _packet()
    store = FilesystemLaneDeltaPacketStore(tmp_path)

    with pytest.raises(StaleLaneSourceError, match="stale"):
        store.persist(
            packet,
            current_source_lane=_lane("new-source"),
            current_destination_lane=packet.destination_lane,
        )
    with pytest.raises(LaneDeltaIntegrityError, match="destination"):
        store.persist(
            packet,
            current_source_lane=packet.source_lane,
            current_destination_lane=_lane("new-destination"),
        )

    assert not (tmp_path / "review" / "lane-deltas-v1").exists()


def test_detects_tampering_and_wrong_expected_lane(tmp_path: Path) -> None:
    packet = _packet()
    store = FilesystemLaneDeltaPacketStore(tmp_path)
    store.persist(
        packet,
        current_source_lane=packet.source_lane,
        current_destination_lane=packet.destination_lane,
    )

    with pytest.raises(StaleLaneSourceError, match="source digest"):
        store.load(
            packet.packet_id,
            expected_source_lane=_lane("other-source"),
            expected_destination_lane=packet.destination_lane,
        )

    path = _stored_path(tmp_path)
    payload = json.loads(path.read_text())
    payload["packet"]["last_shared_turn"] = "turn-tampered"
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(LaneDeltaIntegrityError, match="digest"):
        store.load(
            packet.packet_id,
            expected_source_lane=packet.source_lane,
            expected_destination_lane=packet.destination_lane,
        )


def test_rejects_noncanonical_or_oversized_stored_bytes(tmp_path: Path) -> None:
    packet = _packet()
    store = FilesystemLaneDeltaPacketStore(tmp_path)
    store.persist(
        packet,
        current_source_lane=packet.source_lane,
        current_destination_lane=packet.destination_lane,
    )
    path = _stored_path(tmp_path)
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(LaneDeltaIntegrityError, match="canonical"):
        store.load(
            packet.packet_id,
            expected_source_lane=packet.source_lane,
            expected_destination_lane=packet.destination_lane,
        )

    path.write_bytes(b"x" * (128 * 1024 + 1))
    with pytest.raises(LaneDeltaIntegrityError, match="storage limit"):
        store.load(
            packet.packet_id,
            expected_source_lane=packet.source_lane,
            expected_destination_lane=packet.destination_lane,
        )


def test_explicit_content_mode_requires_opt_in_with_independent_limit(
    tmp_path: Path,
) -> None:
    packet = replace(_packet(), content_mode=LaneContentMode.EXPLICIT_CONTENT)

    with pytest.raises(LaneDeltaStorageError, match="require store opt-in"):
        FilesystemLaneDeltaPacketStore(tmp_path).persist(
            packet,
            current_source_lane=packet.source_lane,
            current_destination_lane=packet.destination_lane,
        )

    receipt = FilesystemLaneDeltaPacketStore(
        tmp_path,
        allow_explicit_content=True,
    ).persist(
        packet,
        current_source_lane=packet.source_lane,
        current_destination_lane=packet.destination_lane,
    )
    assert receipt.packet.content_mode is LaneContentMode.EXPLICIT_CONTENT


def test_explicit_content_mode_enforces_its_smaller_size_limit(
    tmp_path: Path,
) -> None:
    bindings = tuple(
        LaneAttachmentBindingV1(
            attachment_id=f"attachment-{index:03d}",
            expected_digest=_digest(f"attachment-{index:03d}"),
            observed_digest=None,
            status=AttachmentBindingStatus.NOT_TRANSFERRED,
        )
        for index in range(128)
    )
    packet = replace(
        _packet(),
        attachment_bindings=bindings,
        content_mode=LaneContentMode.EXPLICIT_CONTENT,
    )

    with pytest.raises(LaneDeltaStorageError, match="independent limit"):
        FilesystemLaneDeltaPacketStore(
            tmp_path,
            allow_explicit_content=True,
        ).persist(
            packet,
            current_source_lane=packet.source_lane,
            current_destination_lane=packet.destination_lane,
        )


def test_concurrent_writers_cannot_replace_an_immutable_packet_id(
    tmp_path: Path,
) -> None:
    packet = _packet()
    conflicting = replace(packet, last_shared_turn="turn-conflicting")
    store = FilesystemLaneDeltaPacketStore(tmp_path)
    barrier = Barrier(2)
    receipts = []
    errors: list[BaseException] = []

    def persist(candidate):
        try:
            barrier.wait(timeout=5)
            receipts.append(
                store.persist(
                    candidate,
                    current_source_lane=candidate.source_lane,
                    current_destination_lane=candidate.destination_lane,
                )
            )
        except BaseException as error:
            errors.append(error)

    threads = [
        Thread(target=persist, args=(candidate,)) for candidate in (packet, conflicting)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert len(receipts) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], LaneDeltaConflictError)
    winner = receipts[0].packet
    assert (
        store.load(
            winner.packet_id,
            expected_source_lane=winner.source_lane,
            expected_destination_lane=winner.destination_lane,
        ).packet
        == winner
    )
