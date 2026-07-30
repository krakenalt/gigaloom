"""Stable normalized harness event contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from gigaloom.contracts.harness import HarnessRequest


class HarnessEventType(str, Enum):
    """Stable event names stored and streamed for harness runs."""

    SESSION_UPDATED = "session.updated"
    RUN_STARTED = "run_started"
    EXTERNAL_THREAD_STARTED = "external_thread_started"
    EXTERNAL_THREAD_STATUS = "external_thread_status"
    EXTERNAL_TURN_STARTED = "external_turn_started"
    EXTERNAL_TURN_COMPLETED = "external_turn_completed"
    MESSAGE_DELTA = "message_delta"
    REASONING_DELTA = "reasoning_delta"
    STDOUT_DELTA = "stdout_delta"
    STDERR_DELTA = "stderr_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_FINISHED = "tool_call_finished"
    COMMAND_COMPLETED = "command_completed"
    GENERATED_FILE = "generated_file"
    USAGE = "usage"
    FILE_CHANGED = "file_changed"
    TEST_COMPLETED = "test_completed"
    RAW_REQUEST = "raw_request"
    RAW_RESPONSE = "raw_response"
    WARNING = "warning"
    ERROR = "error"
    MESSAGE_COMPLETED = "message_completed"
    CANCEL_REQUESTED = "cancel_requested"
    RUN_CANCELED = "run_canceled"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True)
class HarnessEvent:
    """Optional normalized event emitted by a harness."""

    type: str
    message: str
    payload: Mapping[str, Any] = field(default_factory=dict)


def emit_event(request: HarnessRequest, event: HarnessEvent) -> bool:
    """Publish a live event when the caller supplied an event sink.

    Return ``True`` when the event was delivered. Harnesses can keep the event in
    ``HarnessResult.events`` when this returns ``False`` so direct CLI callers
    retain the same final event visibility without duplicating live UI events.
    """
    if request.event_sink is None:
        return False
    request.event_sink(event)
    return True


for _public_contract in (HarnessEvent, HarnessEventType, emit_event):
    _public_contract.__module__ = "gigaloom.types"

__all__ = ["HarnessEvent", "HarnessEventType", "emit_event"]
