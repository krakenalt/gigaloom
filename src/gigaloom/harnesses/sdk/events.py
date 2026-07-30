"""Normalized events shared by external agent CLI adapters."""

from __future__ import annotations

import subprocess
from typing import Any, Mapping

from gigaloom.types import Availability, HarnessEvent
from gigaloom.harnesses.sdk._agent_cli_internal import (
    _first_mapping,
    _first_token_count,
)


def normalize_usage(value: Any) -> dict[str, int] | None:
    """Normalize common Codex, Claude, and Gemini token usage shapes."""
    if not isinstance(value, Mapping):
        return None
    nested = value.get("tokens")
    source = nested if isinstance(nested, Mapping) else value
    input_tokens = _first_token_count(
        source,
        "input_tokens",
        "prompt_tokens",
        "prompt",
    )
    output_tokens = _first_token_count(
        source,
        "output_tokens",
        "completion_tokens",
        "candidates",
    )
    total_tokens = _first_token_count(source, "total_tokens", "total")
    prompt_details = _first_mapping(
        source,
        "input_tokens_details",
        "prompt_tokens_details",
    )
    completion_details = _first_mapping(
        source,
        "output_tokens_details",
        "completion_tokens_details",
    )
    cached_input_tokens = _first_token_count(
        source,
        "cached_input_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
    )
    if cached_input_tokens is None:
        cached_input_tokens = _first_token_count(prompt_details, "cached_tokens")
    reasoning_output_tokens = _first_token_count(
        source,
        "reasoning_output_tokens",
        "reasoning_tokens",
        "thoughts_tokens",
    )
    if reasoning_output_tokens is None:
        reasoning_output_tokens = _first_token_count(
            completion_details,
            "reasoning_tokens",
            "thoughts_tokens",
        )
    tool_tokens = _first_token_count(source, "tool_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return {
        key: item
        for key, item in {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_input_tokens": cached_input_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
            "tool_tokens": tool_tokens,
        }.items()
        if item is not None
    }


def usage_event(value: Any) -> HarnessEvent | None:
    """Build one normalized usage event when token counts are available."""
    usage = normalize_usage(value)
    if usage is None:
        return None
    return HarnessEvent(
        type="usage",
        message="Token usage updated.",
        payload=usage,
    )


def message_delta_event(delta: Any) -> HarnessEvent | None:
    """Build one assistant message delta event for non-empty text."""
    if not isinstance(delta, str) or not delta:
        return None
    return HarnessEvent(
        type="message_delta",
        message="Assistant message delta.",
        payload={"delta": delta},
    )


def tool_call_event(
    event_type: str,
    *,
    tool_call_id: Any,
    name: Any = None,
    arguments: Any = None,
    result: Any = None,
    status: Any = None,
    arguments_are_complete: bool = False,
    parent_tool_call_id: Any = None,
    source: Any = None,
) -> HarnessEvent:
    """Build a normalized tool-call lifecycle event."""
    payload = {
        "tool_call_id": str(tool_call_id or "tool-call"),
        "name": str(name) if name is not None else None,
        "status": str(status) if status is not None else None,
        "parent_tool_call_id": (
            str(parent_tool_call_id) if parent_tool_call_id is not None else None
        ),
        "source": str(source) if source is not None else None,
    }
    if event_type == "tool_call_delta":
        payload["arguments" if arguments_are_complete else "arguments_delta"] = (
            arguments
        )
        payload["output_delta"] = result
    else:
        payload["arguments"] = arguments
        payload["result"] = result
    message = {
        "tool_call_started": "Tool call started.",
        "tool_call_delta": "Tool call updated.",
        "tool_call_finished": "Tool call finished.",
    }.get(event_type, "Tool call updated.")
    return HarnessEvent(
        type=event_type,
        message=message,
        payload={key: item for key, item in payload.items() if item is not None},
    )


def executable_availability(
    *,
    executable: str | None,
    executable_name: str,
    install_hint: str,
    version_args: tuple[str, ...] | None = ("--version",),
    source: str | None = None,
) -> Availability:
    """Return availability for an executable, optionally probing startup."""
    if executable is None:
        return Availability.missing(
            f"{executable_name} executable not found",
            install_hint,
        )
    source_detail = f" via {source}" if source else ""
    if version_args is None:
        return Availability.available(
            f"{executable_name} executable found{source_detail}: {executable}"
        )
    try:
        completed = subprocess.run(
            (executable, *version_args),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Availability.error(
            f"{executable_name} executable failed to run",
            str(exc),
        )
    detail = (completed.stdout or completed.stderr).strip().splitlines()
    version = detail[0] if detail else None
    if completed.returncode != 0:
        return Availability.error(
            f"{executable_name} executable failed to run",
            version,
        )
    suffix = f" ({version})" if version else ""
    return Availability.available(
        f"{executable_name} executable found{source_detail}: {executable}{suffix}"
    )
