"""Private process and streaming lifecycle helpers for agent CLI adapters."""

from __future__ import annotations

import json
import os
from queue import Full, Queue
import signal
import subprocess
import threading
import time
from typing import IO, Any, Mapping

from gpt2giga_harness.types import (
    REDACTED,
    HarnessEvent,
    HarnessRequest,
    redact_secrets,
)
from gpt2giga_harness.harnesses.sdk.agent_cli import (
    PROCESS_GROUP_GRACE_SECONDS,
    PROCESS_STOP_TIMEOUT_SECONDS,
    RAW_CAPTURE_CHARS,
    SECRET_ENV_KEYS,
    STREAM_EVENT_BATCH_CHARS,
    STREAM_EVENT_BATCH_SECONDS,
    STREAM_POLL_SECONDS,
    TERMINAL_ERROR_CHARS,
    StreamPayloadParser,
    StreamTerminalOutcome,
    stream_terminal_failure,
)


def _redact_known_secret_values(text: str, env: Mapping[str, str]) -> str:
    redacted = text
    for key in SECRET_ENV_KEYS:
        value = env.get(key)
        if value and value != "0":
            redacted = redacted.replace(value, REDACTED)
    return redacted


def _redact_output_text(text: str, env: Mapping[str, str]) -> str:
    return str(redact_secrets(_redact_known_secret_values(text, env)))


def _safe_json_line(payload: Mapping[str, Any]) -> str:
    return json.dumps(redact_secrets(dict(payload)), ensure_ascii=False) + "\n"


def _bounded_append(current: str, value: str) -> str:
    return (current + value)[-RAW_CAPTURE_CHARS:]


def _start_stream_reader(
    stream: IO[str] | None,
    stream_name: str,
    output_queue: Queue[tuple[str, str | None]],
    stop_event: threading.Event,
) -> threading.Thread:
    def enqueue(item: tuple[str, str | None]) -> None:
        while not stop_event.is_set():
            try:
                output_queue.put(item, timeout=STREAM_POLL_SECONDS)
                return
            except Full:
                continue

    def read_stream() -> None:
        try:
            if stream is not None:
                for line in iter(stream.readline, ""):
                    enqueue((stream_name, line))
                    if stop_event.is_set():
                        break
        except (OSError, ValueError):
            # Shutdown may close the pipe while the daemon reader is draining it.
            pass
        finally:
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    # The process or another cleanup path may already own the pipe.
                    pass
            enqueue((stream_name, None))

    thread = threading.Thread(
        target=read_stream,
        name=f"gpt2giga-{stream_name}-reader",
        daemon=True,
    )
    thread.start()
    return thread


def _stop_process(process: subprocess.Popen[str]) -> None:
    if os.name == "posix":
        _stop_posix_process_group(process)
        return
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        # The child may exit between poll() and terminate().
        pass
    if _wait_for_process(process, PROCESS_STOP_TIMEOUT_SECONDS):
        return
    try:
        process.kill()
    except OSError:
        # The child may exit before the forced-kill fallback runs.
        pass
    _wait_for_process(process, PROCESS_STOP_TIMEOUT_SECONDS)


def _wait_for_process(
    process: subprocess.Popen[str],
    timeout_seconds: float,
) -> bool:
    if process.poll() is not None:
        return True
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return process.poll() is not None
    return True


def _signal_process_group(process_group_id: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(process_group_id, sig)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _stop_posix_process_group(process: subprocess.Popen[str]) -> None:
    process_group_id = process.pid
    signaled_group = _signal_process_group(process_group_id, signal.SIGTERM)
    if not signaled_group and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            # The direct child may exit while its process group is being stopped.
            pass

    # Reap the direct child with a bounded grace period. Descendants can keep the
    # inherited stdout/stderr descriptors open even after the direct child exits,
    # so always follow with a group-wide SIGKILL while that session still exists.
    _wait_for_process(process, PROCESS_STOP_TIMEOUT_SECONDS)
    group_deadline = time.monotonic() + PROCESS_GROUP_GRACE_SECONDS
    while _process_group_exists(process_group_id) and time.monotonic() < group_deadline:
        time.sleep(STREAM_POLL_SECONDS)
    if _process_group_exists(process_group_id):
        _signal_process_group(process_group_id, signal.SIGKILL)
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            # The direct child may exit after the group-wide signal.
            pass
        _wait_for_process(process, PROCESS_STOP_TIMEOUT_SECONDS)


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _close_finished_process_pipes(
    process: subprocess.Popen[str],
    readers: tuple[threading.Thread, threading.Thread],
) -> None:
    for stream, reader in zip((process.stdout, process.stderr), readers, strict=True):
        # TextIOWrapper.close() may wait on the same lock held by a reader blocked
        # in readline(). Leaving that daemon reader to close its own pipe is safer
        # than making cancellation or timeout unbounded on platforms without
        # process-group signaling.
        if reader.is_alive():
            continue
        if stream is None or stream.closed:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            # Reader shutdown can close the pipe before this cleanup pass.
            pass


def _cancel_requested(request: HarnessRequest) -> bool:
    cancel_event = getattr(request, "cancel_event", None)
    is_set = getattr(cancel_event, "is_set", None)
    return bool(callable(is_set) and is_set())


class _StreamEventCoalescer:
    """Batch adjacent text deltas before invoking a persistence-backed sink."""

    def __init__(
        self,
        request: HarnessRequest,
        pending_events: list[HarnessEvent],
    ) -> None:
        self._request = request
        self._pending_events = pending_events
        self._pending: HarnessEvent | None = None
        self._pending_since = 0.0

    def record(self, event: HarnessEvent) -> None:
        now = time.monotonic()
        if not _coalescible_delta(event):
            self.flush()
            self._dispatch(event)
            return

        if self._pending is not None and _can_coalesce(self._pending, event):
            self._pending = _merge_delta_events(self._pending, event)
        else:
            self.flush()
            self._pending = event
            self._pending_since = now

        delta = _coalescible_delta(self._pending)
        if delta is not None and (
            len(delta) >= STREAM_EVENT_BATCH_CHARS
            or now - self._pending_since >= STREAM_EVENT_BATCH_SECONDS
        ):
            self.flush()

    def flush_due(self) -> None:
        if self._pending is None:
            return
        if time.monotonic() - self._pending_since >= STREAM_EVENT_BATCH_SECONDS:
            self.flush()

    def flush(self) -> None:
        if self._pending is None:
            return
        pending = self._pending
        self._pending = None
        self._pending_since = 0.0
        self._dispatch(pending)

    def _dispatch(self, event: HarnessEvent) -> None:
        event_sink = getattr(self._request, "event_sink", None)
        if callable(event_sink):
            event_sink(event)
        else:
            self._pending_events.append(event)


def _record_stream_event(
    event: HarnessEvent,
    *,
    message_parts: list[str],
    usage: dict[str, int],
    tool_calls: dict[str, dict[str, Any]],
    event_coalescer: _StreamEventCoalescer,
) -> None:
    safe_event = _safe_stream_event(event)
    payload = safe_event.payload
    if safe_event.type == "message_delta":
        delta = payload.get("delta")
        if isinstance(delta, str):
            message_parts.append(delta)
    elif safe_event.type == "usage":
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_input_tokens",
            "reasoning_output_tokens",
            "tool_tokens",
        ):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                usage[key] = value
    elif safe_event.type.startswith("tool_call_"):
        _update_tool_call(tool_calls, safe_event)
    event_coalescer.record(safe_event)


def _coalescible_delta(event: HarnessEvent) -> str | None:
    if event.type not in {"message_delta", "stdout_delta", "stderr_delta"}:
        return None
    delta = event.payload.get("delta")
    return delta if isinstance(delta, str) else None


def _can_coalesce(left: HarnessEvent, right: HarnessEvent) -> bool:
    if left.type != right.type or left.message != right.message:
        return False
    left_payload = {key: value for key, value in left.payload.items() if key != "delta"}
    right_payload = {
        key: value for key, value in right.payload.items() if key != "delta"
    }
    return left_payload == right_payload


def _merge_delta_events(left: HarnessEvent, right: HarnessEvent) -> HarnessEvent:
    left_delta = _coalescible_delta(left) or ""
    right_delta = _coalescible_delta(right) or ""
    return HarnessEvent(
        type=left.type,
        message=left.message,
        payload={**left.payload, "delta": left_delta + right_delta},
    )


def _safe_stream_event(event: HarnessEvent) -> HarnessEvent:
    return HarnessEvent(
        type=event.type,
        message=str(redact_secrets(event.message)),
        payload=dict(redact_secrets(dict(event.payload))),
    )


def _update_tool_call(
    tool_calls: dict[str, dict[str, Any]],
    event: HarnessEvent,
) -> None:
    payload = event.payload
    identifier = str(payload.get("tool_call_id") or f"tool-{len(tool_calls)}")
    tool_call = tool_calls.setdefault(identifier, {"tool_call_id": identifier})
    for key in ("name", "result", "status"):
        if payload.get(key) is not None:
            tool_call[key] = payload[key]
    output_delta = payload.get("output_delta")
    if isinstance(output_delta, str):
        previous_output = tool_call.get("result")
        tool_call["result"] = (
            previous_output + output_delta
            if isinstance(previous_output, str)
            else output_delta
        )
    arguments = (
        payload.get("arguments_delta")
        if event.type == "tool_call_delta"
        else payload.get("arguments")
    )
    if arguments is None:
        return
    if event.type == "tool_call_delta" and isinstance(arguments, str):
        previous = tool_call.get("arguments")
        tool_call["arguments"] = (
            previous + arguments if isinstance(previous, str) else arguments
        )
    else:
        tool_call["arguments"] = arguments


def _complete_usage(usage: Mapping[str, int]) -> dict[str, int]:
    completed = dict(usage)
    if (
        "total_tokens" not in completed
        and {
            "input_tokens",
            "output_tokens",
        }
        <= completed.keys()
    ):
        completed["total_tokens"] = (
            completed["input_tokens"] + completed["output_tokens"]
        )
    return completed


def _first_token_count(value: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool):
            return item
    return None


def _first_mapping(value: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    for key in keys:
        item = value.get(key)
        if isinstance(item, Mapping):
            return item
    return {}


def _stream_terminal_outcome(
    parse_payload: StreamPayloadParser,
) -> StreamTerminalOutcome | None:
    outcome = getattr(parse_payload, "terminal_outcome", None)
    if isinstance(outcome, StreamTerminalOutcome):
        return outcome
    recognized_payloads = getattr(parse_payload, "recognized_payloads", None)
    if recognized_payloads == 0:
        return stream_terminal_failure(
            None,
            fallback="Structured CLI output did not contain a recognized event contract",
        )
    return None


def _poll_stream_parser_events(
    parse_payload: StreamPayloadParser,
) -> tuple[HarnessEvent, ...]:
    poll_events = getattr(parse_payload, "poll_events", None)
    if not callable(poll_events):
        return ()
    try:
        events = poll_events()
    except (OSError, RuntimeError, TypeError, ValueError):
        return ()
    if not isinstance(events, (list, tuple)):
        return ()
    return tuple(event for event in events if isinstance(event, HarnessEvent))


def _concise_stream_error(value: Any, *, fallback: str) -> str:
    safe_value = redact_secrets(value)
    text = _stream_error_text(safe_value)
    normalized = " ".join(text.split()) if text else ""
    return (normalized or fallback)[:TERMINAL_ERROR_CHARS]


def _stream_error_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("message", "error", "detail", "reason"):
            if key in value and (text := _stream_error_text(value[key])):
                return text
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple)):
        parts = [text for item in value if (text := _stream_error_text(item))]
        return "; ".join(parts[:3])
    if value is None:
        return ""
    return str(value)


def _extract_machine_readable_text(stdout: str) -> str:
    payload = _load_json_object(stdout)
    if payload is None:
        return ""
    for key in ("result", "response", "output_text", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _load_json_object(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(decoded, dict):
        return decoded
    return None
