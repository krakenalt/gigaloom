"""Strict binary WebSocket boundary for managed terminal attach."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import json
from typing import Any, Mapping


MAX_TERMINAL_INPUT_BYTES = 64 * 1024
MAX_TERMINAL_INPUT_CHUNK_BYTES = 4096
MAX_TERMINAL_CONTROL_CHARS = 512
MAX_TERMINAL_OUTPUT_FRAME_BYTES = 64 * 1024
MAX_TERMINAL_OUTPUT_QUEUE_BYTES = 1024 * 1024
MIN_RESIZE_ROWS = 2
MAX_RESIZE_ROWS = 200
MIN_RESIZE_COLUMNS = 20
MAX_RESIZE_COLUMNS = 500


class TerminalWebSocketCloseCode(IntEnum):
    """Application close codes for the managed terminal WebSocket."""

    PROTOCOL_ERROR = 4400
    FORBIDDEN = 4403
    NOT_FOUND = 4404
    DETACHED = 4410
    EXITED = 4411
    BACKPRESSURE = 4429
    INTERNAL_ERROR = 4500


@dataclass(frozen=True)
class TerminalInputFrame:
    """One bounded byte-exact browser input frame."""

    data: bytes


@dataclass(frozen=True)
class TerminalResizeFrame:
    """One revision-bound validated terminal resize frame."""

    rows: int
    columns: int
    revision: int


TerminalClientFrame = TerminalInputFrame | TerminalResizeFrame


def parse_terminal_client_frame(frame: bytes | str) -> TerminalClientFrame:
    """Parse binary input or the one admitted text resize control frame."""
    if isinstance(frame, bytes):
        if not frame or len(frame) > MAX_TERMINAL_INPUT_BYTES:
            raise ValueError("terminal input frame size is invalid")
        return TerminalInputFrame(data=frame)
    if not isinstance(frame, str) or len(frame) > MAX_TERMINAL_CONTROL_CHARS:
        raise ValueError("terminal control frame size is invalid")
    try:
        payload = json.loads(frame)
    except json.JSONDecodeError as exc:
        raise ValueError("terminal control frame is invalid") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("terminal control frame must be an object")
    if set(payload) != {"type", "rows", "columns", "revision"}:
        raise ValueError("terminal control frame fields are invalid")
    if payload.get("type") != "resize":
        raise ValueError("terminal control frame type is invalid")
    rows = _strict_int(payload.get("rows"), field_name="terminal rows")
    columns = _strict_int(payload.get("columns"), field_name="terminal columns")
    revision = _strict_int(
        payload.get("revision"),
        field_name="terminal revision",
    )
    if not MIN_RESIZE_ROWS <= rows <= MAX_RESIZE_ROWS:
        raise ValueError("terminal rows are invalid")
    if not MIN_RESIZE_COLUMNS <= columns <= MAX_RESIZE_COLUMNS:
        raise ValueError("terminal columns are invalid")
    if revision < 1:
        raise ValueError("terminal revision is invalid")
    return TerminalResizeFrame(
        rows=rows,
        columns=columns,
        revision=revision,
    )


def terminal_resize_frame_json(
    *,
    rows: int,
    columns: int,
    revision: int,
) -> str:
    """Build the canonical resize control-frame JSON."""
    parsed = parse_terminal_client_frame(
        json.dumps(
            {
                "type": "resize",
                "rows": rows,
                "columns": columns,
                "revision": revision,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if not isinstance(parsed, TerminalResizeFrame):
        raise AssertionError("resize frame serialization failed")
    return json.dumps(
        {
            "columns": parsed.columns,
            "revision": parsed.revision,
            "rows": parsed.rows,
            "type": "resize",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _strict_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} is invalid")
    return value
