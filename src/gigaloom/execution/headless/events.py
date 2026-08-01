"""Canonical ANSI-free JSONL framing for headless runs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import io
import threading
from typing import BinaryIO, Mapping

from gigaloom.contracts import (
    HeadlessEventKind,
    HeadlessEventV1,
    HeadlessTerminalReceiptV1,
    headless_event_to_dict,
)
from gigaloom.contracts.operational_validation import canonical_json_bytes
from gigaloom.execution.headless.contracts import HeadlessProgressSinkPort


MAX_HEADLESS_EVENT_LINE_BYTES = 72 * 1024
_BACKEND_EVENT_KINDS = frozenset(
    {
        HeadlessEventKind.TURN_STARTED,
        HeadlessEventKind.TOOL_ACTIVITY,
        HeadlessEventKind.APPROVAL_REQUIRED,
        HeadlessEventKind.USAGE,
        HeadlessEventKind.ARTIFACT,
        HeadlessEventKind.WARNING,
    }
)


class HeadlessEventStreamError(RuntimeError):
    """Content-free JSONL stream failure."""


class CanonicalJsonlEventWriter:
    """Synchronous event writer with direct bounded backpressure."""

    def __init__(
        self,
        *,
        run_id: str,
        stream: BinaryIO,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.run_id = run_id
        self._stream = stream
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._next_sequence = 0
        self._terminal_event: HeadlessEventV1 | None = None
        self._closed = False
        self._lock = threading.Lock()

    @property
    def next_sequence(self) -> int:
        """Return the sequence that will be assigned to the next event."""
        with self._lock:
            return self._next_sequence

    @property
    def last_sequence(self) -> int | None:
        """Return the last fully flushed sequence, if any."""
        with self._lock:
            return self._next_sequence - 1 if self._next_sequence else None

    def emit_kind(
        self,
        kind: HeadlessEventKind,
        payload: Mapping[str, object],
        *,
        content_capture: bool = False,
    ) -> HeadlessEventV1:
        """Create, serialize, and flush one canonical event."""
        with self._lock:
            if self._closed or self._terminal_event is not None:
                raise HeadlessEventStreamError("headless event stream is closed")
            event = HeadlessEventV1(
                sequence=self._next_sequence,
                run_id=self.run_id,
                timestamp=self._clock(),
                kind=kind,
                payload=payload,
                content_capture=content_capture,
            )
            self._write_event(event)
            self._next_sequence += 1
            if kind.terminal:
                self._terminal_event = event
            return event

    def close(self, receipt: HeadlessTerminalReceiptV1) -> None:
        """Verify that one flushed terminal event matches its receipt."""
        with self._lock:
            terminal = self._terminal_event
            if self._closed:
                raise HeadlessEventStreamError("headless event stream already closed")
            if terminal is None:
                raise HeadlessEventStreamError(
                    "headless event stream has no terminal event"
                )
            if (
                receipt.run_id != self.run_id
                or receipt.final_sequence != terminal.sequence
                or receipt.terminal_kind is not terminal.kind
            ):
                raise HeadlessEventStreamError(
                    "headless terminal receipt does not match the stream"
                )
            self._closed = True

    def _write_event(self, event: HeadlessEventV1) -> None:
        line = canonical_json_bytes(headless_event_to_dict(event)) + b"\n"
        if len(line) > MAX_HEADLESS_EVENT_LINE_BYTES or b"\x1b" in line:
            raise HeadlessEventStreamError("headless event line violates bounds")
        try:
            _write_all(self._stream, line)
            self._stream.flush()
        except (OSError, ValueError, io.UnsupportedOperation) as error:
            raise HeadlessEventStreamError(
                "headless event stream write failed"
            ) from error


class RunnerOwnedProgressSink(HeadlessProgressSinkPort):
    """Restrict backends to non-terminal, content-free progress events."""

    def __init__(self, writer: CanonicalJsonlEventWriter) -> None:
        self._writer = writer

    def emit(
        self,
        kind: HeadlessEventKind,
        payload: Mapping[str, object],
        *,
        content_capture: bool = False,
    ) -> None:
        """Emit one admitted backend progress event."""
        if kind not in _BACKEND_EVENT_KINDS:
            raise ValueError("backend cannot emit runner-owned headless event kinds")
        if content_capture:
            raise ValueError("headless content capture is not admitted")
        self._writer.emit_kind(kind, payload, content_capture=False)


class NullHeadlessProgressSink(HeadlessProgressSinkPort):
    """Validate progress kinds when no wire stream was requested."""

    def emit(
        self,
        kind: HeadlessEventKind,
        payload: Mapping[str, object],
        *,
        content_capture: bool = False,
    ) -> None:
        """Reject invalid kinds or capture without retaining output."""
        if kind not in _BACKEND_EVENT_KINDS:
            raise ValueError("backend cannot emit runner-owned headless event kinds")
        if content_capture:
            raise ValueError("headless content capture is not admitted")
        HeadlessEventV1(
            sequence=0,
            run_id="validation-run",
            timestamp=datetime.now(timezone.utc),
            kind=kind,
            payload=payload,
            content_capture=False,
        )


def emit_unadmitted_terminal(
    *,
    run_id: str,
    reason_code: str,
    stream: BinaryIO,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Emit one parseable terminal event when invocation admission fails."""
    writer = CanonicalJsonlEventWriter(run_id=run_id, stream=stream, clock=clock)
    writer.emit_kind(
        HeadlessEventKind.RUN_FAILED,
        {
            "result_ref": "headless-result.json",
            "capsule_ref": None,
            "omissions": [
                "invocation_not_admitted",
                "result_artifact_unavailable",
                "terminal_receipt_unavailable",
            ],
            "reason_code": reason_code,
        },
    )


def _write_all(stream: BinaryIO, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = stream.write(view)
        if written is None or written <= 0:
            raise OSError("short headless event write")
        view = view[written:]


__all__ = [
    "CanonicalJsonlEventWriter",
    "HeadlessEventStreamError",
    "MAX_HEADLESS_EVENT_LINE_BYTES",
    "NullHeadlessProgressSink",
    "RunnerOwnedProgressSink",
    "emit_unadmitted_terminal",
]
