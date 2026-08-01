"""Mechanical lane-delta builder contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest

from gigaloom.contracts import (
    LaneContentMode,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTurnRangeV1,
    lane_delta_packet_to_dict,
)
from gigaloom.review.handoffs.lane_delta import (
    LANE_IDENTITY_FIELD_COUNT,
    LaneDeltaBuildError,
    LaneDeltaBuildRequestV1,
    LaneDeltaBuilder,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _lane(**changes: object) -> LaneIdentityV1:
    values: dict[str, object] = {
        "agent_id": "codex",
        "route_id": "native",
        "model_id": "model-a",
        "account_ref": "account-a",
        "session_id": "session-a",
        "workspace_fingerprint": _digest("workspace-a"),
        "policy_digest": _digest("policy-a"),
        "context_manifest_digest": _digest("manifest-a"),
        "run_capsule_digest": _digest("capsule-a"),
    }
    values.update(changes)
    return LaneIdentityV1(**values)  # type: ignore[arg-type]


def _request(
    source: LaneIdentityV1,
    destination: LaneIdentityV1,
    **changes: object,
) -> LaneDeltaBuildRequestV1:
    values: dict[str, object] = {
        "source_lane": source,
        "destination_lane": destination,
        "last_shared_turn": "turn-7",
        "missed_turn_range": LaneTurnRangeV1(8, 10),
        "disclosure_mode": LaneDisclosureMode.PACKET,
        "created_at": NOW,
    }
    values.update(changes)
    return LaneDeltaBuildRequestV1(**values)  # type: ignore[arg-type]


def test_builds_deterministic_content_free_identity_delta() -> None:
    source = _lane()
    destination = _lane(
        agent_id="qwen-code",
        route_id="acp-v1",
        model_id="model-b",
        account_ref="account-b",
        session_id="session-b",
        workspace_fingerprint=_digest("workspace-b"),
        policy_digest=_digest("policy-b"),
        context_manifest_digest=_digest("manifest-b"),
        run_capsule_digest=_digest("capsule-b"),
    )
    request = _request(source, destination)

    first = LaneDeltaBuilder().build(request)
    second = LaneDeltaBuilder().build(request)

    assert first is not None
    assert first == second
    assert len(first.changed_anchors) == LANE_IDENTITY_FIELD_COUNT == 9
    assert {item.reason_code for item in first.changed_anchors} == {
        "agent_id_changed",
        "route_id_changed",
        "model_id_changed",
        "account_ref_changed",
        "session_id_changed",
        "workspace_fingerprint_changed",
        "policy_digest_changed",
        "context_manifest_digest_changed",
        "run_capsule_digest_changed",
    }
    payload = lane_delta_packet_to_dict(first)
    assert payload["content_mode"] == "content_free"
    assert "content" not in payload
    assert "prompt" not in str(payload).lower()


@pytest.mark.parametrize(
    ("field_name", "destination_value", "reason_code"),
    [
        ("agent_id", "qwen-code", "agent_id_changed"),
        ("route_id", "acp-v1", "route_id_changed"),
        ("model_id", "model-b", "model_id_changed"),
        ("account_ref", "account-b", "account_ref_changed"),
        ("session_id", "session-b", "session_id_changed"),
    ],
)
def test_each_public_lane_selector_creates_a_new_lane(
    field_name: str,
    destination_value: str,
    reason_code: str,
) -> None:
    source = _lane()
    destination = replace(source, **{field_name: destination_value})

    packet = LaneDeltaBuilder().build(_request(source, destination))

    assert packet is not None
    assert [item.reason_code for item in packet.changed_anchors] == [reason_code]


def test_unchanged_lane_creates_no_packet() -> None:
    lane = _lane()

    assert LaneDeltaBuilder().build(_request(lane, lane)) is None


def test_mechanical_builder_rejects_unbound_explicit_content() -> None:
    with pytest.raises(LaneDeltaBuildError, match="separate content admission"):
        _request(
            _lane(),
            _lane(agent_id="qwen-code"),
            content_mode=LaneContentMode.EXPLICIT_CONTENT,
        )


def test_builder_work_is_constant_in_history_size() -> None:
    source = _lane()
    destination = _lane(agent_id="qwen-code")

    packet = LaneDeltaBuilder().build(_request(source, destination))

    assert packet is not None
    assert len(packet.changed_anchors) <= LANE_IDENTITY_FIELD_COUNT
