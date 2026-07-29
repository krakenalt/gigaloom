"""Filesystem persistence for bounded active-session event appenders."""

from __future__ import annotations

from collections.abc import Iterable
import json
import os
from pathlib import Path
from typing import Any

from gpt2giga_harness.sessions.event_persistence import (
    BoundedSessionEventAppender,
    SessionEventAppender,
    event_forces_flush,
)
from gpt2giga_harness.sessions.locking import exclusive_file_lock
from gpt2giga_harness.sessions.models import (
    HarnessStoredEvent,
    event_to_dict,
    session_from_dict,
)
from gpt2giga_harness.sessions.redaction import redact_for_storage
from gpt2giga_harness.sessions.store import SessionNotFoundError

EVENTS_FILE = "events.jsonl"
MANIFEST_FILE = "manifest.json"


class FilesystemEventPersistenceMixin:
    """Persist synchronous event groups through one validated session path."""

    def append_event(self, event: HarnessStoredEvent) -> HarnessStoredEvent:
        return self.event_appender(event.session_id).append(event)

    def append_events(
        self,
        events: Iterable[HarnessStoredEvent],
    ) -> tuple[HarnessStoredEvent, ...]:
        records = tuple(events)
        if not records:
            return ()
        return self.event_appender(records[0].session_id).append_many(records)

    def event_appender(self, session_id: str) -> SessionEventAppender:
        session_dir = self._session_dir(session_id)
        try:
            session_from_dict(_read_json(session_dir / MANIFEST_FILE))
        except FileNotFoundError as exc:
            self._session_locator.forget(session_id)
            raise SessionNotFoundError(session_id) from exc
        return BoundedSessionEventAppender(
            session_id,
            lambda events: self._append_prepared_events(
                session_dir / EVENTS_FILE,
                events,
            ),
        )

    def _append_prepared_events(
        self,
        path: Path,
        events: tuple[HarnessStoredEvent, ...],
        *,
        publish: bool = True,
    ) -> tuple[HarnessStoredEvent, ...]:
        if not events:
            return ()
        with self._read_index_lock:
            index = self._read_index
            complete = index is not None and index.records_complete()
            if complete:
                index.mark_records_complete(False)
            offsets = self._append_event_jsonl_batch(path, events)
            if index is not None:
                for event, offset in zip(events, offsets, strict=True):
                    index.record_event(event, offset)
                if complete:
                    index.mark_records_complete(True)
        if publish:
            for event in events:
                self.event_broker.publish(event)
        return events

    def _append_event_jsonl_batch(
        self,
        path: Path,
        events: tuple[HarnessStoredEvent, ...],
    ) -> tuple[int, ...]:
        if len(events) == 1:
            return (self._append_jsonl(path, event_to_dict(events[0])),)
        return _append_jsonl_batch(path, events)


def _append_jsonl_batch(
    path: Path,
    events: tuple[HarnessStoredEvent, ...],
) -> tuple[int, ...]:
    encoded = tuple(
        (
            json.dumps(
                redact_for_storage(event_to_dict(event)),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for event in events
    )
    with exclusive_file_lock(path):
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            position = os.lseek(descriptor, 0, os.SEEK_END)
            offsets: list[int] = []
            pending: list[bytes] = []
            for event, payload in zip(events, encoded, strict=True):
                offsets.append(position)
                position += len(payload)
                pending.append(payload)
                if event_forces_flush(event):
                    _write_all(descriptor, b"".join(pending))
                    os.fsync(descriptor)
                    pending.clear()
            if pending:
                _write_all(descriptor, b"".join(pending))
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return tuple(offsets)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("event append made no write progress")
        remaining = remaining[written:]


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return payload
