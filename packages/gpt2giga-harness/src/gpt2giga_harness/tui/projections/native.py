"""Typed native projections for TUI clients."""

from __future__ import annotations

import re
from typing import Any, Mapping


from gpt2giga_harness.tui.contracts import (
    MAX_NATIVE_SCROLLBACK_CHARS,
    MAX_NATIVE_INPUT_CHARS,
    WorkbenchClientError,
    NativeTerminalSnapshot,
)

from gpt2giga_harness.tui.projections.values import (
    _BIDI_CONTROL_RE,
    _CONTROL_RE,
    _bounded_non_negative_int,
    _mapping,
    _mapping_items,
    _display_text,
    _required_identity,
    _path_identity,
    _neutralize_presentation_text,
)


_FULLSCREEN_TERMINAL_RE = re.compile(
    r"\x1b(?:"
    r"\][^\x07\x1b]*(?:\x07|\x1b\\)?|"
    r"P.*?(?:\x1b\\|$)|"
    r"\[[0-9;?]*(?:[ABCDEFGHJKSTf]|[hl])"
    r")",
    re.DOTALL,
)


def _native_terminal_snapshot_from_mapping(
    data: Mapping[str, Any],
) -> NativeTerminalSnapshot:
    process = _mapping(data.get("process"))
    run = _mapping(data.get("run"))
    raw_parts = tuple(
        str(item.get("text") or "") for item in _mapping_items(data.get("outputs"), 512)
    )
    raw_output = "".join(raw_parts)
    handoff_required = bool(_FULLSCREEN_TERMINAL_RE.search(raw_output))
    safe_output = neutralize_native_terminal_output(raw_output)
    output_truncated = bool(data.get("truncated")) or (
        len(safe_output) > MAX_NATIVE_SCROLLBACK_CHARS
    )
    safe_output = safe_output[-MAX_NATIVE_SCROLLBACK_CHARS:]
    return NativeTerminalSnapshot(
        process_id=_required_identity(
            process.get("id") or data.get("process_id"), "native process id"
        ),
        session_id=_required_identity(
            process.get("session_id") or run.get("session_id"), "session id"
        ),
        run_id=_required_identity(process.get("run_id") or run.get("id"), "run id"),
        harness_id=_display_text(
            process.get("harness_id") or run.get("harness_id") or "unknown"
        ),
        transport=_display_text(process.get("transport") or "unknown"),
        status=_display_text(
            data.get("status")
            or process.get("status")
            or run.get("status")
            or "unknown"
        ),
        cursor=_bounded_non_negative_int(
            data.get("cursor")
            if data.get("cursor") is not None
            else process.get("terminal_cursor")
        ),
        output=safe_output,
        output_truncated=output_truncated,
        exit_code=_optional_int(
            data.get("exit_code")
            if data.get("exit_code") is not None
            else process.get("exit_code")
        ),
        handoff_required=handoff_required,
    )


def neutralize_native_terminal_output(value: Any) -> str:
    """Remove terminal-control semantics while preserving bounded visible text."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return _neutralize_presentation_text(text)


def _native_terminal_input(value: Any) -> str:
    if not isinstance(value, str):
        raise WorkbenchClientError("native terminal input must be text")
    if not value or len(value) > MAX_NATIVE_INPUT_CHARS:
        raise WorkbenchClientError(
            f"native terminal input must contain 1-{MAX_NATIVE_INPUT_CHARS} characters"
        )
    if (
        _CONTROL_RE.search(value)
        or _BIDI_CONTROL_RE.search(value)
        or "\r" in value
        or "\n" in value
    ):
        raise WorkbenchClientError("native terminal input contains terminal controls")
    return value


def _native_terminal_dimensions(rows: Any, columns: Any) -> tuple[int, int]:
    if (
        not isinstance(rows, int)
        or isinstance(rows, bool)
        or not 2 <= rows <= 200
        or not isinstance(columns, int)
        or isinstance(columns, bool)
        or not 20 <= columns <= 500
    ):
        raise WorkbenchClientError(
            "native terminal dimensions require rows 2-200 and columns 20-500"
        )
    return rows, columns


def _non_negative_cursor(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WorkbenchClientError("native terminal cursor must be non-negative")
    return value


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _in_process_native_terminal_error(
    process_id: str, *, cursor: int | None = None
) -> WorkbenchClientError:
    _path_identity(process_id)
    if cursor is not None:
        _non_negative_cursor(cursor)
    return WorkbenchClientError(
        "native terminal process control requires attach mode; the in-process "
        "presentation does not read PTYs or runtime stores directly"
    )
