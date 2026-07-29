"""Bounded server-sent event decoding for attach mode."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence


from gpt2giga_harness.tui.contracts import WorkbenchClientError

_RUN_RESNAPSHOT_EVENT_TYPES = frozenset(
    {
        "approval_requested",
        "approval_decided",
        "cancel_requested",
        "error",
        "input_requested",
        "question_requested",
        "run_canceled",
        "run_failed",
        "run_finished",
    }
)


@dataclass(frozen=True)
class _SseFrame:
    """One bounded decoded server-sent event."""

    event: str
    id: str | None
    data: Mapping[str, Any]


def _decode_sse_frame(
    event: str,
    event_id: str | None,
    data_lines: Sequence[str],
) -> _SseFrame:
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError as exc:
        raise WorkbenchClientError("attach stream returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise WorkbenchClientError("attach stream event must be an object")
    return _SseFrame(event=event, id=event_id, data=dict(payload))


def _event_requires_run_resnapshot(value: Mapping[str, Any]) -> bool:
    event_type = str(value.get("type") or "").lower().replace("-", "_")
    return event_type in _RUN_RESNAPSHOT_EVENT_TYPES
