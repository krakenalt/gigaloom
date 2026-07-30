"""Cursor and serialization contracts for UI event streams."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any, Mapping

from gpt2giga_harness.arena import HarnessArenaChildRun, HarnessArenaRun
from gpt2giga_harness.sessions import HarnessSessionStore, SessionNotFoundError
from gpt2giga_harness.sessions.event_stream import EventCursorPosition
from gpt2giga_harness.sessions.models import (
    HarnessRun,
    HarnessStoredEvent,
    event_to_dict,
)


def event_response(event: HarnessStoredEvent) -> dict[str, Any]:
    """Serialize one stored event for a bounded JSON response."""
    return event_to_dict(event)


def run_sse_event(event: HarnessStoredEvent, cursor: str) -> str:
    """Serialize one stored run event as SSE."""
    data = json.dumps(event_response(event), ensure_ascii=False)
    return f"id: {cursor}\ndata: {data}\n\n"


def run_resnapshot_sse(run: HarnessRun, cursor: str) -> str:
    """Serialize a bounded resnapshot instruction for a slow consumer."""
    payload = {
        "type": "resnapshot_required",
        "reason": "slow_consumer",
        "cursor": cursor,
        "snapshot_url": f"/api/cockpit/sessions/{run.session_id}/events",
        "stream_url": f"/api/runs/{run.id}/events/stream",
    }
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: resnapshot\nid: {cursor}\ndata: {data}\n\n"


def resolve_run_stream_cursor(
    store: HarnessSessionStore,
    run: HarnessRun,
    value: str | None,
    *,
    tail_only: bool = False,
) -> EventCursorPosition:
    """Resolve a durable event position from a public cursor."""
    if value is None:
        if tail_only:
            resolver = getattr(store, "event_tail_offset", None)
            if not callable(resolver):
                raise ValueError("session store does not support durable event tails")
            return EventCursorPosition(
                offset=resolver(run.session_id),
                terminal_seen=False,
            )
        return EventCursorPosition(offset=0, terminal_seen=False)
    if value.startswith("hc1."):
        return decode_run_stream_cursor(value, run)
    resolver = getattr(store, "resolve_event_cursor", None)
    if not callable(resolver):
        raise ValueError("session store does not support durable event cursors")
    position = resolver(run.session_id, run_id=run.id, event_id=value)
    if position is None:
        raise ValueError("event cursor is stale; fetch a bounded snapshot")
    return position


def encode_run_stream_cursor(
    run: HarnessRun,
    offset: int,
    *,
    terminal_event_seen: bool,
) -> str:
    """Encode a run-scoped durable event position."""
    scope = hashlib.sha256(f"{run.session_id}\0{run.id}".encode()).hexdigest()[:16]
    payload = json.dumps(
        {
            "v": 1,
            "scope": scope,
            "offset": max(offset, 0),
            "terminal": terminal_event_seen,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "hc1." + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_run_stream_cursor(
    value: str,
    run: HarnessRun,
) -> EventCursorPosition:
    """Decode and validate a run-scoped durable event position."""
    try:
        encoded = value.removeprefix("hc1.")
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        scope = hashlib.sha256(f"{run.session_id}\0{run.id}".encode()).hexdigest()[:16]
        if (
            not isinstance(payload, Mapping)
            or payload.get("v") != 1
            or payload.get("scope") != scope
        ):
            raise ValueError
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError
    except (
        binascii.Error,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid or cross-run event cursor") from exc
    return EventCursorPosition(
        offset=offset,
        terminal_seen=bool(payload.get("terminal")),
    )


def native_sse_cursor(last_event_id: str | None) -> int:
    """Parse a native-output cursor, falling back to the beginning."""
    value = _optional_text(last_event_id)
    if value is None:
        return 0
    try:
        return max(int(value), 0)
    except ValueError:
        return 0


def native_output_sse(payload: Mapping[str, Any]) -> str:
    """Serialize one native process output payload as SSE."""
    cursor = max(int(payload.get("cursor") or 0), 0)
    data = json.dumps(payload, ensure_ascii=False)
    return f"id: {cursor}\ndata: {data}\n\n"


def arena_events(
    arena: HarnessArenaRun,
    store: HarnessSessionStore,
    *,
    after_id: str | None = None,
) -> list[tuple[HarnessArenaChildRun, HarnessStoredEvent]]:
    """Collect stable, ordered child events for an Arena stream."""
    events: list[tuple[HarnessArenaChildRun, HarnessStoredEvent]] = []
    for child in arena.child_runs:
        if child.run_id is None or child.session_id is None:
            continue
        try:
            child_events = store.list_events(child.session_id, run_id=child.run_id)
        except SessionNotFoundError:
            continue
        events.extend((child, event) for event in child_events)
    events.sort(key=lambda item: (item[1].created_at, item[0].index, item[1].id))
    if after_id is None:
        return events
    seen = False
    filtered: list[tuple[HarnessArenaChildRun, HarnessStoredEvent]] = []
    for item in events:
        if seen:
            filtered.append(item)
        elif item[1].id == after_id:
            seen = True
    return filtered


def arena_sse_event(
    arena: HarnessArenaRun,
    child: HarnessArenaChildRun,
    event: HarnessStoredEvent,
) -> str:
    """Serialize one Arena child event as SSE."""
    payload = {
        "id": event.id,
        "arena_id": arena.id,
        "child_index": child.index,
        "harness_id": child.harness_id,
        "type": event.type,
        "message": event.message,
        "payload": dict(event.payload),
        "created_at": event.created_at,
        "event": event_to_dict(event),
    }
    data = json.dumps(payload, ensure_ascii=False)
    return f"id: {event.id}\ndata: {data}\n\n"


def run_status_is_terminal(status: str) -> bool:
    """Return whether a run status closes an event stream."""
    return status in {"succeeded", "failed", "canceled"}


def arena_status_is_terminal(status: str) -> bool:
    """Return whether an Arena status closes an event stream."""
    return status in {"succeeded", "failed", "partial", "canceled"}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
