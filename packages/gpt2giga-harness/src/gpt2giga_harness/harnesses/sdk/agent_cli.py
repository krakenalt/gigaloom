"""Shared helpers for external agent CLI harnesses."""

# ruff: noqa: E402, F401

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from queue import Empty, Full, Queue
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Any, Callable, Mapping

from gpt2giga_harness import proxy
from gpt2giga_harness.gigachat_compatibility import (
    gigachat_gateway_ready_event,
)
from gpt2giga_harness.types import (
    REDACTED,
    Availability,
    HarnessContext,
    HarnessEvent,
    HarnessRequest,
    HarnessResult,
    redact_secrets,
)

SAFE_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SHELL", "LANG", "LC_ALL")
SECRET_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GEMINI_API_KEY",
    "GPT2GIGA_API_KEY",
    "OPENAI_API_KEY",
)
RAW_CAPTURE_CHARS = 4000
STREAM_POLL_SECONDS = 0.05
STREAM_QUEUE_MAX_ITEMS = 256
STREAM_EVENT_BATCH_CHARS = 256
STREAM_EVENT_BATCH_SECONDS = 0.08
PROCESS_STOP_TIMEOUT_SECONDS = 2.0
PROCESS_GROUP_GRACE_SECONDS = 0.2
PROCESS_STREAM_DRAIN_TIMEOUT_SECONDS = 1.0
PROCESS_READER_JOIN_TIMEOUT_SECONDS = 0.5
TERMINAL_ERROR_CHARS = 1000

StreamPayloadParser = Callable[[Mapping[str, Any]], tuple[HarnessEvent, ...]]


@dataclass(frozen=True)
class StreamTerminalOutcome:
    """Describe the terminal status reported by a structured CLI stream."""

    ok: bool
    error: str | None = None


def stream_terminal_failure(
    value: Any,
    *,
    fallback: str,
) -> StreamTerminalOutcome:
    """Build a concise redacted failure reported by a structured CLI stream."""
    return StreamTerminalOutcome(
        ok=False,
        error=_concise_stream_error(value, fallback=fallback),
    )


def build_safe_env(
    context: HarnessContext,
    *,
    extra: Mapping[str, str] | None = None,
    home: str | None = None,
) -> dict[str, str]:
    """Build a minimal environment that excludes upstream GigaChat secrets."""
    env: dict[str, str] = {
        key: value
        for key in SAFE_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }
    env.update(context.extra_env)
    if home is not None:
        env["HOME"] = home
    if extra is not None:
        env.update(extra)
    return env


def workspace_error(value: str | None) -> str | None:
    """Return a user-facing workspace validation error, if any."""
    if value is None:
        return None
    path = Path(value)
    if not path.exists():
        return f"Workspace does not exist: {value}"
    if not path.is_dir():
        return f"Workspace is not a directory: {value}"
    return None


def prepare_proxy_for_agent(
    request: HarnessRequest,
    context: HarnessContext,
    *,
    harness_id: str,
    command: tuple[str, ...],
) -> tuple[HarnessContext, tuple[HarnessEvent, ...], HarnessResult | None]:
    """Ensure the local proxy is ready before launching an external agent CLI."""
    startup = proxy.ensure_proxy_available(context, request.api_mode)
    if not startup.ok:
        return (
            context,
            (),
            HarnessResult(
                ok=False,
                text="",
                raw={
                    "proxy_url": context.proxy_url,
                    "auto_start_proxy": context.auto_start_proxy,
                },
                command=command,
                error=startup.error or "proxy is not reachable",
            ),
        )

    prepared_context = replace(
        context,
        api_key=startup.api_key or context.api_key,
        harness_model_key=(startup.harness_model_key or context.harness_model_key),
    )
    events: tuple[HarnessEvent, ...] = ()
    if startup.started:
        events = (
            HarnessEvent(
                type="proxy_sidecar",
                message="Started local gpt2giga proxy sidecar.",
                payload={
                    "proxy_url": context.proxy_url,
                    "pid": startup.pid,
                    "detail": startup.detail,
                },
            ),
        )
    events = (
        *events,
        gigachat_gateway_ready_event(
            request,
            harness_id=harness_id,
            sidecar_started=startup.started,
        ),
    )
    return prepared_context, events, None


def with_events(
    result: HarnessResult,
    events: tuple[HarnessEvent, ...],
) -> HarnessResult:
    """Return a result with prepended events."""
    if not events:
        return result
    return HarnessResult(
        ok=result.ok,
        text=result.text,
        raw=result.raw,
        events=(*events, *result.events),
        command=result.command,
        error=result.error,
    )


def with_raw_metadata(
    result: HarnessResult,
    metadata: Mapping[str, Any] | None,
) -> HarnessResult:
    """Return a result with additional redaction-safe execution evidence."""
    if not metadata:
        return result
    return HarnessResult(
        ok=result.ok,
        text=result.text,
        raw={**dict(result.raw), **dict(metadata)},
        events=result.events,
        command=result.command,
        error=result.error,
    )


def run_command(
    *,
    label: str,
    command: tuple[str, ...],
    env: Mapping[str, str],
    cwd: str | None,
    timeout_seconds: float,
) -> HarnessResult:
    """Run a command and normalize the captured result."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return HarnessResult(
            ok=False,
            text="",
            raw={"timeout_seconds": timeout_seconds},
            command=command,
            error=f"{label} timed out after {exc.timeout} seconds",
        )
    except OSError as exc:
        return HarnessResult(
            ok=False,
            text="",
            raw={},
            command=command,
            error=f"{label} failed to start: {exc}",
        )

    stdout = _redact_known_secret_values(completed.stdout, env)
    stderr = _redact_known_secret_values(completed.stderr, env)
    payload = _load_json_object(stdout)
    normalized_usage = (
        normalize_usage(payload.get("usage") or payload.get("stats"))
        if payload is not None
        else None
    )
    normalized_usage_event = usage_event(normalized_usage)
    text = _extract_machine_readable_text(stdout) or stdout.strip() or stderr.strip()
    raw = {
        "exit_code": completed.returncode,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }
    if normalized_usage is not None:
        raw["usage"] = normalized_usage
    return HarnessResult(
        ok=completed.returncode == 0,
        text=text,
        raw=raw,
        events=(normalized_usage_event,) if normalized_usage_event is not None else (),
        command=command,
        error=None if completed.returncode == 0 else text,
    )


def run_streaming_command(
    *,
    label: str,
    command: tuple[str, ...],
    env: Mapping[str, str],
    cwd: str | None,
    timeout_seconds: float,
    request: HarnessRequest,
    parse_payload: StreamPayloadParser,
) -> HarnessResult:
    """Run a JSONL command while emitting normalized events as output arrives."""
    if _cancel_requested(request):
        return HarnessResult(
            ok=False,
            text="",
            raw={"usage": {}, "tool_calls": []},
            command=command,
            error=f"{label} canceled",
        )
    process_group_kwargs: dict[str, Any] = {}
    if os.name == "posix":
        process_group_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **process_group_kwargs,
        )
    except OSError as exc:
        return HarnessResult(
            ok=False,
            text="",
            raw={"usage": {}, "tool_calls": []},
            command=command,
            error=f"{label} failed to start: {exc}",
        )

    if request.process_sink is not None:
        try:
            request.process_sink(
                {
                    "process_id": process.pid,
                    "process_group_id": (
                        os.getpgid(process.pid) if os.name == "posix" else None
                    ),
                }
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            # Process reporting is best-effort and must not abort the harness run.
            pass

    output_queue: Queue[tuple[str, str | None]] = Queue(maxsize=STREAM_QUEUE_MAX_ITEMS)
    reader_stop = threading.Event()
    readers = (
        _start_stream_reader(process.stdout, "stdout", output_queue, reader_stop),
        _start_stream_reader(process.stderr, "stderr", output_queue, reader_stop),
    )
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    finished_streams: set[str] = set()
    message_parts: list[str] = []
    usage: dict[str, int] = {}
    tool_calls: dict[str, dict[str, Any]] = {}
    pending_events: list[HarnessEvent] = []
    event_coalescer = _StreamEventCoalescer(request, pending_events)
    stdout = ""
    stderr = ""
    canceled = False
    timed_out = False
    stream_drain_deadline: float | None = None

    try:
        while process.poll() is None or len(finished_streams) < 2:
            now = time.monotonic()
            process_running = process.poll() is None
            if process_running:
                stream_drain_deadline = None
                if _cancel_requested(request):
                    canceled = True
                    _stop_process(process)
                elif now >= deadline:
                    timed_out = True
                    _stop_process(process)
            elif stream_drain_deadline is None:
                stream_drain_deadline = now + PROCESS_STREAM_DRAIN_TIMEOUT_SECONDS
            elif now >= stream_drain_deadline:
                _stop_process(process)
                break

            for event in _poll_stream_parser_events(parse_payload):
                _record_stream_event(
                    event,
                    message_parts=message_parts,
                    usage=usage,
                    tool_calls=tool_calls,
                    event_coalescer=event_coalescer,
                )

            try:
                stream_name, line = output_queue.get(timeout=STREAM_POLL_SECONDS)
            except Empty:
                event_coalescer.flush_due()
                continue
            if process.poll() is not None:
                stream_drain_deadline = (
                    time.monotonic() + PROCESS_STREAM_DRAIN_TIMEOUT_SECONDS
                )
            if line is None:
                finished_streams.add(stream_name)
                continue

            clean_line = _redact_output_text(line, env)
            if stream_name == "stderr":
                stderr = _bounded_append(stderr, clean_line)
                _record_stream_event(
                    HarnessEvent(
                        type="stderr_delta",
                        message=f"{label} stderr delta.",
                        payload={"delta": clean_line},
                    ),
                    message_parts=message_parts,
                    usage=usage,
                    tool_calls=tool_calls,
                    event_coalescer=event_coalescer,
                )
                continue

            parsed = _load_json_object(clean_line)
            if parsed is None:
                stdout = _bounded_append(stdout, clean_line)
                _record_stream_event(
                    HarnessEvent(
                        type="stdout_delta",
                        message=f"{label} stdout delta.",
                        payload={"delta": clean_line},
                    ),
                    message_parts=message_parts,
                    usage=usage,
                    tool_calls=tool_calls,
                    event_coalescer=event_coalescer,
                )
                continue

            stdout = _bounded_append(stdout, _safe_json_line(parsed))
            try:
                events = parse_payload(parsed)
            except Exception:
                events = (
                    HarnessEvent(
                        type="stdout_delta",
                        message=f"{label} emitted an unrecognized JSON event.",
                        payload={"delta": _safe_json_line(parsed)},
                    ),
                )
            for event in events:
                _record_stream_event(
                    event,
                    message_parts=message_parts,
                    usage=usage,
                    tool_calls=tool_calls,
                    event_coalescer=event_coalescer,
                )
    finally:
        if process.poll() is None:
            _stop_process(process)
        for event in _poll_stream_parser_events(parse_payload):
            _record_stream_event(
                event,
                message_parts=message_parts,
                usage=usage,
                tool_calls=tool_calls,
                event_coalescer=event_coalescer,
            )
        reader_stop.set()
        for reader in readers:
            reader.join(timeout=PROCESS_READER_JOIN_TIMEOUT_SECONDS)
        _close_finished_process_pipes(process, readers)
        event_coalescer.flush()

    return_code = process.poll()
    if return_code is None:
        _stop_process(process)
        return_code = process.poll()
    exit_code = int(return_code if return_code is not None else -1)

    text = "".join(message_parts).strip()
    if not text:
        text = (
            _extract_machine_readable_text(stdout) or stdout.strip() or stderr.strip()
        )
    normalized_usage = _complete_usage(usage)
    normalized_tool_calls = list(tool_calls.values())
    raw = {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "usage": normalized_usage,
        "tool_calls": normalized_tool_calls,
    }
    if canceled:
        return HarnessResult(
            ok=False,
            text="",
            raw=raw,
            events=tuple(pending_events),
            command=command,
            error=f"{label} canceled",
        )
    if timed_out:
        return HarnessResult(
            ok=False,
            text="",
            raw={**raw, "timeout_seconds": timeout_seconds},
            events=tuple(pending_events),
            command=command,
            error=f"{label} timed out after {timeout_seconds} seconds",
        )
    terminal_outcome = _stream_terminal_outcome(parse_payload)
    terminal_error = (
        terminal_outcome.error
        if terminal_outcome is not None and not terminal_outcome.ok
        else None
    )
    ok = exit_code == 0 and terminal_error is None
    return HarnessResult(
        ok=ok,
        text=text,
        raw=raw,
        events=tuple(pending_events),
        command=command,
        error=None
        if ok
        else terminal_error or text or f"{label} exited with status {exit_code}",
    )


from gpt2giga_harness.harnesses.sdk.events import (
    executable_availability,
    message_delta_event,
    normalize_usage,
    tool_call_event,
    usage_event,
)
from gpt2giga_harness.harnesses.sdk._agent_cli_internal import (
    _StreamEventCoalescer,
    _bounded_append,
    _cancel_requested,
    _close_finished_process_pipes,
    _complete_usage,
    _concise_stream_error,
    _extract_machine_readable_text,
    _load_json_object,
    _poll_stream_parser_events,
    _record_stream_event,
    _redact_known_secret_values,
    _redact_output_text,
    _safe_json_line,
    _start_stream_reader,
    _stop_process,
    _stream_terminal_outcome,
)
