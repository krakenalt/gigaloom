"""Run lifecycle integration for verified content-free lane deltas."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from gigaloom.contracts import (
    LaneContentMode,
    LaneDisclosureMode,
    LaneIdentityV1,
    LaneTurnRangeV1,
)
from gigaloom.contracts.lane_delta_codec import lane_identity_from_dict
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.execution.api import RunCompletionArtifactV1, RunCompletionHook
from gigaloom.review.capsules.api import FilesystemRunCapsuleRepository
from gigaloom.sessions import HarnessRun, HarnessSession, HarnessSessionStore

from .builder import LaneDeltaBuildRequestV1, LaneDeltaBuilder
from .planning import (
    LaneDeltaLifecycleError,
    destination_selection,
    explicit_selectors as resolve_explicit_selectors,
    identity,
    mapping,
)
from .storage import FilesystemLaneDeltaPacketStore


LANE_DELTA_METADATA_KEY = "lane_delta"
LANE_PLAN_METADATA_KEY = "lane_delta_plan"
LANE_STATE_METADATA_KEY = "verified_lane_state"
LANE_DELTA_REFERENCE_KIND = "gigaloom.lane_delta.reference.v1"
LANE_PLAN_KIND = "gigaloom.lane_delta.plan.v1"
LANE_STATE_KIND = "gigaloom.verified_lane_state.v1"

_LANE_PUBLIC_SELECTORS = (
    "account_ref",
    "agent_id",
    "model_id",
    "route_id",
    "session_id",
)
_SELECTION_FIELDS = (
    "agent_id",
    "route_id",
    "model_id",
    "account_ref",
    "session_id",
    "workspace_fingerprint",
    "policy_digest",
    "context_manifest_digest",
)
_MAX_TURN_SEQUENCE = 2**63 - 1


class LaneDeltaLifecycleService(RunCompletionHook):
    """Plan one bounded lane comparison and finalize it after Run Capsule capture."""

    def __init__(
        self,
        *,
        session_store: HarnessSessionStore,
        builder: LaneDeltaBuilder,
        packet_store: FilesystemLaneDeltaPacketStore,
        capsule_repository: FilesystemRunCapsuleRepository,
        delegate: RunCompletionHook | None = None,
    ) -> None:
        self._session_store = session_store
        self._builder = builder
        self._packet_store = packet_store
        self._capsule_repository = capsule_repository
        self._delegate = delegate

    def prepare_run_metadata(
        self,
        *,
        session: HarnessSession,
        options: Mapping[str, Any],
        payload: Mapping[str, Any],
        provider_account_binding: Mapping[str, Any] | None,
        existing_run: HarnessRun | None = None,
    ) -> dict[str, Any]:
        """Freeze a constant-size source and destination plan before execution."""
        if existing_run is not None:
            retained = mapping(existing_run.metadata.get(LANE_PLAN_METADATA_KEY))
            if retained:
                source, _destination, _selectors, _sequence = _parse_plan(retained)
                _verify_current_source(session, source)
                return {LANE_PLAN_METADATA_KEY: dict(retained)}

        source = _optional_state(session.metadata.get(LANE_STATE_METADATA_KEY))
        selection = destination_selection(
            session=session,
            options=options,
            payload=payload,
            provider_account_binding=provider_account_binding,
        )
        explicit_selectors = resolve_explicit_selectors(
            session=session,
            payload=payload,
            source=source,
            destination=selection,
        )
        sequence = 1 if source is None else _turn_sequence(source) + 1
        if sequence > _MAX_TURN_SEQUENCE:
            raise LaneDeltaLifecycleError("lane turn sequence exceeds its bound")
        plan = {
            "schema_version": 1,
            "kind": LANE_PLAN_KIND,
            "source": source,
            "destination": selection,
            "explicit_selectors": list(explicit_selectors),
            "turn_sequence": sequence,
        }
        _parse_plan(plan)
        return {LANE_PLAN_METADATA_KEY: plan}

    def on_run_completed(
        self,
        run_id: str,
        *,
        completed_at: str,
    ) -> tuple[RunCompletionArtifactV1, ...]:
        """Capture delegated artifacts, then emit and verify one changed-lane packet."""
        delegated = (
            self._delegate.on_run_completed(run_id, completed_at=completed_at)
            if self._delegate is not None
            else ()
        )
        run = self._session_store.get_run(run_id)
        plan_payload = mapping(run.metadata.get(LANE_PLAN_METADATA_KEY))
        if not plan_payload:
            return delegated
        source_state, destination, selectors, sequence = _parse_plan(plan_payload)
        session = self._session_store.get_session(run.session_id)
        _verify_current_source(session, source_state)
        capsule_digest, capsule_status = self._capsule_reference(run.id)
        destination_lane = _lane_from_selection(destination, capsule_digest)
        state = _lane_state(
            run=run,
            lane=destination_lane,
            capsule_status=capsule_status,
            turn_sequence=sequence,
        )

        lane_artifact: RunCompletionArtifactV1 | None = None
        lane_reference: dict[str, Any] | None = None
        if source_state is not None:
            source_lane = _source_lane(
                source_state,
                capsule_repository=self._capsule_repository,
            )
            changed_selectors = tuple(
                field_name
                for field_name in selectors
                if getattr(source_lane, field_name)
                != getattr(destination_lane, field_name)
            )
            if changed_selectors:
                packet = self._builder.build(
                    LaneDeltaBuildRequestV1(
                        source_lane=source_lane,
                        destination_lane=destination_lane,
                        last_shared_turn=_last_shared_turn(source_state),
                        missed_turn_range=LaneTurnRangeV1(sequence, sequence),
                        disclosure_mode=LaneDisclosureMode.PACKET,
                        content_mode=LaneContentMode.CONTENT_FREE,
                        created_at=_timestamp(completed_at),
                        attachment_observations=None,
                        declared_omissions=_declared_omissions(
                            source_state,
                            destination_capsule_status=capsule_status,
                        ),
                    )
                )
                if packet is None:  # pragma: no cover - guarded by selector comparison
                    raise LaneDeltaLifecycleError(
                        "changed lane unexpectedly produced no packet"
                    )
                stored = self._packet_store.persist(
                    packet,
                    current_source_lane=source_lane,
                    current_destination_lane=destination_lane,
                )
                verified = self._packet_store.load_by_digests(
                    packet.packet_id,
                    expected_source_lane_digest=source_lane.lane_digest,
                    expected_destination_lane_digest=destination_lane.lane_digest,
                )
                if stored != verified:
                    raise LaneDeltaLifecycleError(
                        "persisted lane packet verification changed its receipt"
                    )
                lane_reference = _lane_reference(
                    stored=stored,
                    changed_selectors=changed_selectors,
                    source_state=source_state,
                    destination_capsule_status=capsule_status,
                )
                lane_artifact = RunCompletionArtifactV1(
                    kind="lane_delta",
                    artifact_id=packet.packet_id,
                    sha256=stored.packet_sha256,
                    status="captured",
                    attributes={
                        "content_free": True,
                        "destination_lane_sha256": destination_lane.lane_digest,
                        "destination_run_capsule_sha256": (
                            destination_lane.run_capsule_digest
                        ),
                        "disclosure_mode": packet.disclosure_mode.value,
                        "hidden_state_portability_claimed": False,
                        "source_lane_sha256": source_lane.lane_digest,
                        "source_run_capsule_sha256": (source_lane.run_capsule_digest),
                    },
                )

        run_metadata = dict(run.metadata)
        run_metadata[LANE_STATE_METADATA_KEY] = state
        if lane_reference is None:
            run_metadata.pop(LANE_DELTA_METADATA_KEY, None)
        else:
            run_metadata[LANE_DELTA_METADATA_KEY] = lane_reference
        self._session_store.update_run(run.id, metadata=run_metadata)

        self._session_store.update_session(
            session.id,
            metadata={
                **dict(session.metadata),
                LANE_STATE_METADATA_KEY: state,
            },
        )
        artifacts = (*delegated, *((lane_artifact,) if lane_artifact else ()))
        return tuple(sorted(artifacts, key=lambda item: (item.kind, item.artifact_id)))

    def _capsule_reference(self, run_id: str) -> tuple[str, str]:
        try:
            record = self._capsule_repository.get_by_run(run_id)
        except KeyError:
            return _omitted_capsule_digest(run_id), "not_captured"
        return record.capsule_sha256, "captured"


def lane_state_for_run(run: HarnessRun) -> dict[str, Any] | None:
    """Return a verified detached lane state suitable for an explicit fork."""
    state = _optional_state(run.metadata.get(LANE_STATE_METADATA_KEY))
    return None if state is None else dict(state)


def planned_changed_selectors(
    run: HarnessRun,
    *,
    source_lane: LaneIdentityV1,
    destination_lane: LaneIdentityV1,
) -> tuple[str, ...]:
    """Verify packet identities against the run plan and return explicit changes."""
    plan = mapping(run.metadata.get(LANE_PLAN_METADATA_KEY))
    if not plan:
        raise LaneDeltaLifecycleError("lane delta packet has no retained run plan")
    source, destination, selectors, _sequence = _parse_plan(plan)
    if source is None:
        raise LaneDeltaLifecycleError("lane delta packet has no planned source lane")
    planned_source = lane_identity_from_dict(mapping(source.get("lane")))
    planned_destination = _lane_from_selection(
        destination,
        destination_lane.run_capsule_digest,
    )
    if planned_source.lane_digest != source_lane.lane_digest:
        raise LaneDeltaLifecycleError("lane delta source does not match its run plan")
    if planned_destination.lane_digest != destination_lane.lane_digest:
        raise LaneDeltaLifecycleError(
            "lane delta destination does not match its run plan"
        )
    changed = tuple(
        field_name
        for field_name in selectors
        if getattr(source_lane, field_name) != getattr(destination_lane, field_name)
    )
    if not changed:
        raise LaneDeltaLifecycleError(
            "lane delta packet has no explicit changed selector"
        )
    return changed


def _parse_plan(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, str], tuple[str, ...], int]:
    if set(payload) != {
        "schema_version",
        "kind",
        "source",
        "destination",
        "explicit_selectors",
        "turn_sequence",
    }:
        raise LaneDeltaLifecycleError("lane delta plan fields are invalid")
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("kind") != LANE_PLAN_KIND
    ):
        raise LaneDeltaLifecycleError("lane delta plan identity is invalid")
    destination_raw = mapping(payload.get("destination"))
    if set(destination_raw) != set(_SELECTION_FIELDS) or any(
        not isinstance(destination_raw[field_name], str)
        for field_name in _SELECTION_FIELDS
    ):
        raise LaneDeltaLifecycleError("lane destination fields are invalid")
    destination = {
        field_name: destination_raw[field_name] for field_name in _SELECTION_FIELDS
    }
    _lane_from_selection(destination, "0" * 64)
    selectors_raw = payload.get("explicit_selectors")
    if not isinstance(selectors_raw, list) or any(
        not isinstance(item, str) for item in selectors_raw
    ):
        raise LaneDeltaLifecycleError("lane explicit selectors are invalid")
    selectors = tuple(selectors_raw)
    if selectors != tuple(sorted(set(selectors))) or not set(selectors) <= set(
        _LANE_PUBLIC_SELECTORS
    ):
        raise LaneDeltaLifecycleError("lane explicit selectors are invalid")
    sequence = payload.get("turn_sequence")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or not 1 <= sequence <= _MAX_TURN_SEQUENCE
    ):
        raise LaneDeltaLifecycleError("lane turn sequence is invalid")
    source = _optional_state(payload.get("source"))
    if source is not None and sequence != _turn_sequence(source) + 1:
        raise LaneDeltaLifecycleError("lane turn sequence does not follow source")
    if source is None and sequence != 1:
        raise LaneDeltaLifecycleError("initial lane turn sequence must be one")
    return source, destination, selectors, sequence


def _optional_state(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    state = mapping(value)
    if set(state) != {
        "schema_version",
        "kind",
        "run_id",
        "normalized_session_id",
        "turn_sequence",
        "last_shared_turn",
        "capsule_status",
        "lane",
    }:
        raise LaneDeltaLifecycleError("verified lane state fields are invalid")
    if (
        type(state.get("schema_version")) is not int
        or state.get("schema_version") != 1
        or state.get("kind") != LANE_STATE_KIND
    ):
        raise LaneDeltaLifecycleError("verified lane state identity is invalid")
    for field_name in ("run_id", "normalized_session_id", "last_shared_turn"):
        value = state.get(field_name)
        if not isinstance(value, str):
            raise LaneDeltaLifecycleError("verified lane state identity is invalid")
        identity("state", value, fallback="")
    if state.get("capsule_status") not in {"captured", "not_captured"}:
        raise LaneDeltaLifecycleError("verified lane capsule status is invalid")
    _turn_sequence(state)
    lane_identity_from_dict(mapping(state.get("lane")))
    return dict(state)


def _source_lane(
    state: Mapping[str, Any],
    *,
    capsule_repository: FilesystemRunCapsuleRepository,
) -> LaneIdentityV1:
    lane = lane_identity_from_dict(mapping(state.get("lane")))
    run_id = str(state["run_id"])
    if state["capsule_status"] == "captured":
        try:
            record = capsule_repository.get_by_run(run_id)
        except KeyError as error:
            raise LaneDeltaLifecycleError(
                "source Run Capsule is no longer available"
            ) from error
        if record.capsule_sha256 != lane.run_capsule_digest:
            raise LaneDeltaLifecycleError("source Run Capsule digest changed")
    elif lane.run_capsule_digest != _omitted_capsule_digest(run_id):
        raise LaneDeltaLifecycleError("source Run Capsule omission digest changed")
    return lane


def _verify_current_source(
    session: HarnessSession,
    source: Mapping[str, Any] | None,
) -> None:
    current = _optional_state(session.metadata.get(LANE_STATE_METADATA_KEY))
    if current != source:
        raise LaneDeltaLifecycleError("lane source changed before execution completed")


def _lane_from_selection(
    selection: Mapping[str, str],
    run_capsule_digest: str,
) -> LaneIdentityV1:
    return LaneIdentityV1(
        agent_id=selection["agent_id"],
        route_id=selection["route_id"],
        model_id=selection["model_id"],
        account_ref=selection["account_ref"],
        session_id=selection["session_id"],
        workspace_fingerprint=selection["workspace_fingerprint"],
        policy_digest=selection["policy_digest"],
        context_manifest_digest=selection["context_manifest_digest"],
        run_capsule_digest=run_capsule_digest,
    )


def _lane_state(
    *,
    run: HarnessRun,
    lane: LaneIdentityV1,
    capsule_status: str,
    turn_sequence: int,
) -> dict[str, Any]:
    state = {
        "schema_version": 1,
        "kind": LANE_STATE_KIND,
        "run_id": run.id,
        "normalized_session_id": run.session_id,
        "turn_sequence": turn_sequence,
        "last_shared_turn": run.id,
        "capsule_status": capsule_status,
        "lane": _lane_to_dict(lane),
    }
    verified = _optional_state(state)
    if verified is None:  # pragma: no cover - state is not optional here
        raise LaneDeltaLifecycleError("verified lane state was not built")
    return verified


def _lane_reference(
    *,
    stored: Any,
    changed_selectors: tuple[str, ...],
    source_state: Mapping[str, Any],
    destination_capsule_status: str,
) -> dict[str, Any]:
    packet = stored.packet
    return {
        "schema_version": 1,
        "kind": LANE_DELTA_REFERENCE_KIND,
        "packet_id": packet.packet_id,
        "packet_sha256": stored.packet_sha256,
        "size_bytes": stored.size_bytes,
        "source_lane_sha256": packet.source_lane.lane_digest,
        "destination_lane_sha256": packet.destination_lane.lane_digest,
        "changed_selectors": list(changed_selectors),
        "changed_anchor_reason_codes": [
            item.reason_code for item in packet.changed_anchors
        ],
        "run_capsule_references": [
            {
                "role": "source",
                "sha256": packet.source_lane.run_capsule_digest,
                "status": source_state["capsule_status"],
            },
            {
                "role": "destination",
                "sha256": packet.destination_lane.run_capsule_digest,
                "status": destination_capsule_status,
            },
        ],
        "disclosure_mode": packet.disclosure_mode.value,
        "content_mode": packet.content_mode.value,
        "content_free": True,
        "hidden_state_portability_claimed": False,
        "omissions": list(packet.omissions),
    }


def _lane_to_dict(value: LaneIdentityV1) -> dict[str, Any]:
    return {
        "schema_version": value.schema_version,
        "agent_id": value.agent_id,
        "route_id": value.route_id,
        "model_id": value.model_id,
        "account_ref": value.account_ref,
        "workspace_fingerprint": value.workspace_fingerprint,
        "policy_digest": value.policy_digest,
        "context_manifest_digest": value.context_manifest_digest,
        "run_capsule_digest": value.run_capsule_digest,
        "session_id": value.session_id,
        "lane_digest": value.lane_digest,
    }


def _declared_omissions(
    source_state: Mapping[str, Any],
    *,
    destination_capsule_status: str,
) -> tuple[str, ...]:
    omissions = {
        "full_session_history_not_read",
        "hidden_state_portability_not_claimed",
    }
    if source_state["capsule_status"] != "captured":
        omissions.add("source_run_capsule_not_captured")
    if destination_capsule_status != "captured":
        omissions.add("destination_run_capsule_not_captured")
    return tuple(sorted(omissions))


def _omitted_capsule_digest(run_id: str) -> str:
    return canonical_digest(
        {
            "kind": "run_capsule_omission",
            "reason_code": "not_captured",
            "run_id": run_id,
        }
    )


def _last_shared_turn(state: Mapping[str, Any]) -> str:
    return identity("turn", state.get("last_shared_turn"), fallback="")


def _turn_sequence(state: Mapping[str, Any]) -> int:
    value = state.get("turn_sequence")
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _MAX_TURN_SEQUENCE
    ):
        raise LaneDeltaLifecycleError("verified lane turn sequence is invalid")
    return value


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LaneDeltaLifecycleError("lane completion timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise LaneDeltaLifecycleError(
            "lane completion timestamp must be timezone-aware"
        )
    return parsed


__all__ = [
    "LANE_DELTA_METADATA_KEY",
    "LANE_DELTA_REFERENCE_KIND",
    "LANE_PLAN_METADATA_KEY",
    "LANE_STATE_METADATA_KEY",
    "LaneDeltaLifecycleError",
    "LaneDeltaLifecycleService",
    "lane_state_for_run",
    "planned_changed_selectors",
]
