"""Typed bounded session write-batch contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from typing import Any, Mapping

from gpt2giga_harness.native import (
    HarnessInvocationMode,
    parse_invocation_mode,
)
from gpt2giga_harness.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessStoredEvent,
    event_from_dict,
    event_to_dict,
    message_from_dict,
    message_to_dict,
)
from gpt2giga_harness.sessions.redaction import (
    redact_event_payload,
    redact_for_storage,
)
from gpt2giga_harness.types import GigaChatApiMode, HarnessCapability

MAX_BATCH_RECORDS = 64
MAX_BATCH_MARKER_BYTES = 1024 * 1024
WRITE_BATCH_SCHEMA = "gigaloom.session-write-batch.v1"


@dataclass(frozen=True, slots=True)
class RunCreate:
    """Typed inputs for one run creation inside a session batch."""

    harness_id: str
    prompt: str
    model: str | None
    api_mode: GigaChatApiMode
    capability: HarnessCapability
    mode: str
    workspace: str | None
    run_id: str | None = None
    invocation_mode: HarnessInvocationMode | str | None = None
    status: str = "queued"
    started_at: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RunPatch:
    """One bounded patch of an existing run."""

    run_id: str
    changes: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SessionWriteBatch:
    """One bounded, recoverable set of writes for an exact session."""

    batch_id: str
    session_id: str
    run_creates: tuple[RunCreate, ...] = ()
    run_patches: tuple[RunPatch, ...] = ()
    messages: tuple[HarnessMessage, ...] = ()
    events: tuple[HarnessStoredEvent, ...] = ()

    @property
    def record_count(self) -> int:
        """Return the number of logical records in this write set."""
        return (
            len(self.run_creates)
            + len(self.run_patches)
            + len(self.messages)
            + len(self.events)
        )


@dataclass(frozen=True, slots=True)
class SessionWriteBatchResult:
    """Persisted records returned in batch field order."""

    runs: tuple[HarnessRun, ...]
    messages: tuple[HarnessMessage, ...]
    events: tuple[HarnessStoredEvent, ...]
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class PreparedSessionWriteBatch:
    """Validated, redacted, JSON-serializable batch."""

    batch: SessionWriteBatch
    marker_payload: Mapping[str, Any]
    marker_bytes: bytes


def prepare_write_batch(batch: SessionWriteBatch) -> PreparedSessionWriteBatch:
    """Validate, redact, and serialize a write set before taking its lock."""
    batch_id = str(batch.batch_id).strip()
    session_id = str(batch.session_id).strip()
    if not batch_id or len(batch_id) > 128:
        raise ValueError("batch_id must contain between 1 and 128 characters")
    if not session_id:
        raise ValueError("session_id is required")
    if not 1 <= batch.record_count <= MAX_BATCH_RECORDS:
        raise ValueError(
            f"session write batch must contain 1..{MAX_BATCH_RECORDS} records"
        )
    messages = tuple(_prepare_message(item, session_id) for item in batch.messages)
    events = tuple(_prepare_event(item, session_id) for item in batch.events)
    run_creates = tuple(_prepare_run_create(item) for item in batch.run_creates)
    run_patches = tuple(_prepare_run_patch(item) for item in batch.run_patches)
    prepared = SessionWriteBatch(
        batch_id=batch_id,
        session_id=session_id,
        run_creates=run_creates,
        run_patches=run_patches,
        messages=messages,
        events=events,
    )
    payload = _batch_to_dict(prepared)
    marker_bytes = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    if len(marker_bytes) > MAX_BATCH_MARKER_BYTES:
        raise ValueError(
            f"session write batch marker exceeds {MAX_BATCH_MARKER_BYTES} bytes"
        )
    return PreparedSessionWriteBatch(prepared, payload, marker_bytes)


def prepared_write_batch_from_marker(
    payload: Mapping[str, Any],
) -> PreparedSessionWriteBatch:
    """Recreate and revalidate a redacted batch from its recovery marker."""
    if payload.get("schema") != WRITE_BATCH_SCHEMA:
        raise ValueError("unsupported session write batch marker")
    run_creates = tuple(
        _run_create_from_dict(item)
        for item in _mapping_items(payload.get("run_creates"))
    )
    run_patches = tuple(
        RunPatch(str(item["run_id"]), dict(_mapping(item.get("changes"))))
        for item in _mapping_items(payload.get("run_patches"))
    )
    batch = SessionWriteBatch(
        batch_id=str(payload["batch_id"]),
        session_id=str(payload["session_id"]),
        run_creates=run_creates,
        run_patches=run_patches,
        messages=tuple(
            message_from_dict(item) for item in _mapping_items(payload.get("messages"))
        ),
        events=tuple(
            event_from_dict(item) for item in _mapping_items(payload.get("events"))
        ),
    )
    return prepare_write_batch(batch)


class InMemorySessionWriteBatchMixin:
    """Apply the same bounded contract to the in-memory compatibility store."""

    def apply_write_batch(
        self,
        batch: SessionWriteBatch,
    ) -> SessionWriteBatchResult:
        prepared = prepare_write_batch(batch).batch
        self.get_session(prepared.session_id)
        runs = [
            _create_run(self, prepared.session_id, item)
            for item in prepared.run_creates
        ]
        runs.extend(
            self.update_run(item.run_id, **dict(item.changes))
            for item in prepared.run_patches
        )
        messages = tuple(self.append_message(item) for item in prepared.messages)
        events = tuple(self.append_event(item) for item in prepared.events)
        return SessionWriteBatchResult(tuple(runs), messages, events)


def _create_run(store: Any, session_id: str, item: RunCreate) -> HarnessRun:
    return store.create_run(
        run_id=item.run_id,
        session_id=session_id,
        harness_id=item.harness_id,
        prompt=item.prompt,
        model=item.model,
        api_mode=item.api_mode,
        capability=item.capability,
        mode=item.mode,
        workspace=item.workspace,
        invocation_mode=item.invocation_mode,
        status=item.status,
        started_at=item.started_at,
        metadata=item.metadata,
    )


def _prepare_message(
    message: HarnessMessage,
    session_id: str,
) -> HarnessMessage:
    if message.session_id != session_id:
        raise ValueError("message belongs to another session")
    return replace(
        message,
        content=str(redact_for_storage(message.content)),
        metadata=_redacted_mapping(message.metadata),
    )


def _prepare_event(
    event: HarnessStoredEvent,
    session_id: str,
) -> HarnessStoredEvent:
    if event.session_id != session_id:
        raise ValueError("event belongs to another session")
    return replace(
        event,
        message=str(redact_for_storage(event.message)),
        payload=redact_event_payload(event.payload),
    )


def _prepare_run_create(item: RunCreate) -> RunCreate:
    return replace(
        item,
        harness_id=str(item.harness_id),
        prompt=str(redact_for_storage(item.prompt)),
        api_mode=(
            item.api_mode
            if isinstance(item.api_mode, GigaChatApiMode)
            else GigaChatApiMode(str(item.api_mode))
        ),
        capability=(
            item.capability
            if isinstance(item.capability, HarnessCapability)
            else HarnessCapability(str(item.capability))
        ),
        invocation_mode=parse_invocation_mode(item.invocation_mode),
        status=str(getattr(item.status, "value", item.status)),
        metadata=_redacted_mapping(item.metadata),
    )


def _prepare_run_patch(item: RunPatch) -> RunPatch:
    run_id = str(item.run_id).strip()
    if not run_id:
        raise ValueError("run patch requires run_id")
    changes = dict(item.changes)
    changes.setdefault(
        "updated_at",
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    if "metadata" in changes:
        changes["metadata"] = _redacted_mapping(_mapping(changes["metadata"]))
    if "error" in changes and changes["error"] is not None:
        changes["error"] = str(redact_for_storage(changes["error"]))
    if "command" in changes:
        changes["command"] = tuple(
            str(redact_for_storage(value)) for value in changes["command"]
        )
    if "status" in changes:
        changes["status"] = str(getattr(changes["status"], "value", changes["status"]))
    return RunPatch(run_id, changes)


def _batch_to_dict(batch: SessionWriteBatch) -> dict[str, Any]:
    return {
        "schema": WRITE_BATCH_SCHEMA,
        "batch_id": batch.batch_id,
        "session_id": batch.session_id,
        "run_creates": [_run_create_to_dict(item) for item in batch.run_creates],
        "run_patches": [
            {
                "run_id": item.run_id,
                "changes": _json_mapping(item.changes),
            }
            for item in batch.run_patches
        ],
        "messages": [message_to_dict(item) for item in batch.messages],
        "events": [event_to_dict(item) for item in batch.events],
    }


def _run_create_to_dict(item: RunCreate) -> dict[str, Any]:
    return {
        "run_id": item.run_id,
        "harness_id": item.harness_id,
        "prompt": item.prompt,
        "model": item.model,
        "api_mode": item.api_mode.value,
        "capability": item.capability.value,
        "mode": item.mode,
        "workspace": item.workspace,
        "invocation_mode": parse_invocation_mode(item.invocation_mode).value,
        "status": str(getattr(item.status, "value", item.status)),
        "started_at": item.started_at,
        "metadata": _json_mapping(item.metadata),
    }


def _run_create_from_dict(payload: Mapping[str, Any]) -> RunCreate:
    return RunCreate(
        run_id=str(payload["run_id"]) if payload.get("run_id") is not None else None,
        harness_id=str(payload["harness_id"]),
        prompt=str(payload["prompt"]),
        model=str(payload["model"]) if payload.get("model") is not None else None,
        api_mode=GigaChatApiMode(str(payload["api_mode"])),
        capability=HarnessCapability(str(payload["capability"])),
        mode=str(payload["mode"]),
        workspace=(
            str(payload["workspace"]) if payload.get("workspace") is not None else None
        ),
        invocation_mode=HarnessInvocationMode(str(payload["invocation_mode"])),
        status=str(payload["status"]),
        started_at=(
            str(payload["started_at"])
            if payload.get("started_at") is not None
            else None
        ),
        metadata=dict(_mapping(payload.get("metadata"))),
    )


def _json_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    serialized = json.loads(json.dumps(redact_for_storage(dict(value or {}))))
    return dict(serialized) if isinstance(serialized, Mapping) else {}


def _redacted_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    redacted = redact_for_storage(dict(value or {}))
    return dict(redacted) if isinstance(redacted, Mapping) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))
