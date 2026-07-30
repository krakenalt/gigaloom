"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

import re
from typing import Any

from gigaloom.native.process import NativeProcessRef, NativeProcessStatus
from gigaloom.runtime.models import RunStatus
from gigaloom.sessions import HarnessSessionStore, SessionNotFoundError
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessStoredEvent,
)
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.types import HarnessEventType

_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)?)")
_NATIVE_ERROR_OUTPUT_LIMIT = 4000


def _ensure_native_process_error_message(
    store: HarnessSessionStore, run: HarnessRun, process_ref: NativeProcessRef
) -> None:
    messages = store.list_messages(run.session_id)
    if any(
        (message.run_id == run.id and message.role == "error" for message in messages)
    ):
        return
    content = _native_process_error_content(store, run, process_ref)
    store.append_message(
        HarnessMessage(
            id=new_id("msg"),
            session_id=run.session_id,
            run_id=run.id,
            role="error",
            content=content,
            created_at=utc_now(),
            harness_id=run.harness_id,
            model=run.model,
            api_mode=run.api_mode,
            metadata={
                "source": "native_process",
                "process_id": process_ref.id,
                "exit_code": process_ref.exit_code,
            },
        )
    )
    store.append_event(
        HarnessStoredEvent(
            id=new_id("evt"),
            session_id=run.session_id,
            run_id=run.id,
            type=HarnessEventType.ERROR.value,
            message="Native process failed.",
            payload={
                "process_id": process_ref.id,
                "exit_code": process_ref.exit_code,
                "role": "error",
            },
            created_at=utc_now(),
        )
    )


def _native_process_error_content(
    store: HarnessSessionStore, run: HarnessRun, process_ref: NativeProcessRef
) -> str:
    summary = run.error or "Native process failed"
    output = "".join(
        (
            str(event.payload.get("text") or "")
            for event in store.list_events(run.session_id, run_id=run.id)
            if event.type == "terminal_output"
            and event.payload.get("process_id") == process_ref.id
        )
    )
    output = _ANSI_ESCAPE_RE.sub("", output)
    output = "".join(
        (
            character
            for character in output
            if character in "\n\r\t" or ord(character) >= 32
        )
    ).strip()
    if not output:
        return summary
    excerpt = output[-_NATIVE_ERROR_OUTPUT_LIMIT:]
    indented = "\n".join((f"    {line}" for line in excerpt.splitlines()))
    return f"{summary}.\n\nTerminal output:\n\n{indented}"


def _existing_run_metadata(
    store: HarnessSessionStore, process_ref: NativeProcessRef
) -> dict[str, Any]:
    try:
        for run in store.list_runs(process_ref.session_id):
            if run.id == process_ref.run_id:
                return dict(run.metadata)
    except SessionNotFoundError:
        return {}
    return {}


def _run_status_from_process(process_ref: NativeProcessRef) -> RunStatus:
    if process_ref.status is NativeProcessStatus.RUNNING:
        return RunStatus.RUNNING
    if process_ref.status is NativeProcessStatus.STOPPED:
        return RunStatus.CANCELED
    if process_ref.status is NativeProcessStatus.FAILED:
        return RunStatus.FAILED
    if process_ref.status in {
        NativeProcessStatus.TIMED_OUT,
        NativeProcessStatus.INTERRUPTED,
        NativeProcessStatus.UNKNOWN,
    }:
        return RunStatus.FAILED
    if process_ref.exit_code == 0:
        return RunStatus.SUCCEEDED
    return RunStatus.FAILED


def _native_timeout_seconds(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("timeout_seconds must be a positive number")
    return timeout
