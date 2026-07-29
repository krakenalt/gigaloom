"""Filesystem execution and recovery for bounded session write batches."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from gpt2giga_harness.sessions.locking import exclusive_file_lock
from gpt2giga_harness.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessStoredEvent,
    message_to_dict,
)
from gpt2giga_harness.sessions.redaction import redact_for_storage
from gpt2giga_harness.sessions.store import new_id
from gpt2giga_harness.sessions.write_batch import (
    PreparedSessionWriteBatch,
    RunCreate,
    SessionWriteBatch,
    SessionWriteBatchResult,
    prepare_write_batch,
    prepared_write_batch_from_marker,
)

_MESSAGES_FILE = "messages.jsonl"
_EVENTS_FILE = "events.jsonl"
_MARKERS_DIR = ".write_batches"
_BATCH_LOCK_TARGET = "session_write_batch"


class FilesystemSessionWriteBatchMixin:
    """Apply bounded batches without claiming multi-file transactions."""

    def apply_write_batch(
        self,
        batch: SessionWriteBatch,
    ) -> SessionWriteBatchResult:
        prepared = prepare_write_batch(batch)
        session_dir = self._session_dir(prepared.batch.session_id)
        marker_path = self._marker_path(prepared.batch)
        needs_marker = _needs_recovery_marker(prepared.batch)
        if needs_marker and any(
            item.run_id is None for item in prepared.batch.run_creates
        ):
            raise ValueError("recoverable batch run creates require explicit run_id")
        with exclusive_file_lock(session_dir / _BATCH_LOCK_TARGET):
            if marker_path.exists():
                existing = prepared_write_batch_from_marker(_read_json(marker_path))
                if existing.marker_bytes != prepared.marker_bytes:
                    raise ValueError("batch_id is already bound to another write set")
                result = self._apply_prepared_batch(
                    existing,
                    session_dir=session_dir,
                    recovering=True,
                )
                marker_path.unlink(missing_ok=True)
                return result
            if needs_marker:
                _write_marker(marker_path, prepared.marker_bytes)
            try:
                result = self._apply_prepared_batch(
                    prepared,
                    session_dir=session_dir,
                    recovering=False,
                )
            except Exception:
                if not needs_marker:
                    marker_path.unlink(missing_ok=True)
                raise
            marker_path.unlink(missing_ok=True)
            return result

    def _recover_write_batches(self) -> None:
        marker_dir = self.sessions_dir / _MARKERS_DIR
        if not marker_dir.exists():
            return
        for marker_path in sorted(marker_dir.glob("*.json")):
            prepared = prepared_write_batch_from_marker(_read_json(marker_path))
            session_dir = self._session_dir(prepared.batch.session_id)
            with exclusive_file_lock(session_dir / _BATCH_LOCK_TARGET):
                self._apply_prepared_batch(
                    prepared,
                    session_dir=session_dir,
                    recovering=True,
                )
                marker_path.unlink(missing_ok=True)

    def _apply_prepared_batch(
        self,
        prepared: PreparedSessionWriteBatch,
        *,
        session_dir: Path,
        recovering: bool,
    ) -> SessionWriteBatchResult:
        batch = prepared.batch
        runs: list[HarnessRun] = []
        for item in batch.run_creates:
            if recovering and item.run_id is not None:
                try:
                    runs.append(self.get_run(item.run_id))
                    continue
                except KeyError:
                    pass
            runs.append(_create_run(self, batch.session_id, item))
        runs.extend(
            self.update_run(item.run_id, **dict(item.changes))
            for item in batch.run_patches
        )
        messages = self._append_message_batch(
            batch.session_id,
            batch.messages,
            recovering=recovering,
        )
        events = self._append_event_batch(
            batch.events,
            path=session_dir / _EVENTS_FILE,
            recovering=recovering,
        )
        return SessionWriteBatchResult(
            tuple(runs),
            messages,
            events,
            recovered=recovering,
        )

    def _append_message_batch(
        self,
        session_id: str,
        messages: tuple[HarnessMessage, ...],
        *,
        recovering: bool,
    ) -> tuple[HarnessMessage, ...]:
        if not messages:
            return ()
        path = self._session_dir(session_id) / _MESSAGES_FILE
        pending = _missing_records(path, messages) if recovering else messages
        self._append_record_payloads(
            path,
            tuple(message_to_dict(item) for item in pending),
            pending,
            record_type="message",
        )
        return messages

    def _append_event_batch(
        self,
        events: tuple[HarnessStoredEvent, ...],
        *,
        path: Path,
        recovering: bool,
    ) -> tuple[HarnessStoredEvent, ...]:
        if not events:
            return ()
        pending = _missing_records(path, events) if recovering else events
        self._append_prepared_events(
            path,
            pending,
            publish=False,
        )
        for event in events:
            self.event_broker.publish(event)
        return events

    def _append_record_payloads(
        self,
        path: Path,
        payloads: tuple[Mapping[str, Any], ...],
        records: tuple[Any, ...],
        *,
        record_type: str,
    ) -> None:
        if not payloads:
            return
        with self._read_index_lock:
            index = self._read_index
            complete = index is not None and index.records_complete()
            if complete:
                index.mark_records_complete(False)
            offsets = _append_jsonl_batch(path, payloads)
            if index is not None:
                for record, offset in zip(records, offsets, strict=True):
                    if record_type == "message":
                        index.record_message(record, offset)
                    else:
                        index.record_event(record, offset)
                if complete:
                    index.mark_records_complete(True)

    def _marker_path(self, batch: SessionWriteBatch) -> Path:
        digest = hashlib.sha256(
            f"{batch.session_id}\0{batch.batch_id}".encode()
        ).hexdigest()
        return self.sessions_dir / _MARKERS_DIR / f"{digest}.json"


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


def _needs_recovery_marker(batch: SessionWriteBatch) -> bool:
    file_groups = sum(
        (
            bool(batch.run_creates or batch.run_patches),
            bool(batch.messages),
            bool(batch.events),
        )
    )
    return file_groups > 1


def _missing_records(path: Path, records: tuple[Any, ...]) -> tuple[Any, ...]:
    existing = _record_ids(path)
    return tuple(item for item in records if item.id not in existing)


def _record_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    result: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line)
            if isinstance(payload, Mapping) and payload.get("id") is not None:
                result.add(str(payload["id"]))
    return result


def _append_jsonl_batch(
    path: Path,
    payloads: tuple[Mapping[str, Any], ...],
) -> tuple[int, ...]:
    encoded = tuple(
        (
            json.dumps(
                redact_for_storage(dict(payload)),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for payload in payloads
    )
    with exclusive_file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            start = os.lseek(descriptor, 0, os.SEEK_END)
            offsets: list[int] = []
            position = start
            for item in encoded:
                offsets.append(position)
                position += len(item)
            os.write(descriptor, b"".join(encoded))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return tuple(offsets)


def _write_marker(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{new_id('tmp')}")
    descriptor = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temp_path, path)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("session write batch marker must contain an object")
    return payload
