"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from gigaloom.native.models import (
    HarnessInvocationMode,
    NativeSessionRef,
    NativeTranscriptMessage,
    execution_snapshot_from_dict,
    execution_snapshot_to_dict,
)
from gigaloom.native.process import NativeProcessRef, NativeProcessStatus
from gigaloom.native.registry import (
    NativeHistoryConnectorRegistry,
    UnknownNativeHistoryConnectorError,
)
from gigaloom.native.store import NativeSessionIndexStore
from gigaloom.runtime.models import RunStatus
from gigaloom.session_titles import (
    apply_provider_native_title,
    title_diagnostics,
)
from gigaloom.sessions import HarnessSessionStore, RunNotFoundError
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessNativeLink,
    HarnessRun,
    HarnessStoredEvent,
)
from gigaloom.sessions.redaction import redact_for_storage
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.types import HarnessEventType
from gigaloom.ui.services.attachments import (
    metadata_mapping as _metadata_mapping,
)
from gigaloom.ui.services.native_process_errors import (
    _ensure_native_process_error_message,
    _existing_run_metadata,
    _run_status_from_process,
)
from gigaloom.ui.services.native_sessions import (
    _native_import_message_role,
    _redacted_mapping,
)
from gigaloom.ui.services.request_values import optional_text as _optional_text


def _sync_native_process_run(
    store: HarnessSessionStore,
    process_ref: NativeProcessRef,
    *,
    native_registry: NativeHistoryConnectorRegistry | None = None,
    native_index_store: NativeSessionIndexStore | None = None,
):
    status = _run_status_from_process(process_ref)
    metadata = _existing_run_metadata(store, process_ref)
    metadata.update(
        {
            "invocation_mode": HarnessInvocationMode.NATIVE.value,
            "native_process": {
                "id": process_ref.id,
                "pid": process_ref.pid,
                "process_group_id": process_ref.process_group_id,
                "transport": process_ref.transport,
                "status": process_ref.status.value,
                "exit_code": process_ref.exit_code,
                "owner_id": process_ref.owner_id,
                "owner_process_id": process_ref.owner_process_id,
                "heartbeat_at": process_ref.heartbeat_at,
                "leased_until": process_ref.leased_until,
                "timeout_at": process_ref.timeout_at,
                "cancel_requested_at": process_ref.cancel_requested_at,
                "terminal_cursor": process_ref.terminal_cursor,
                "recovery_outcome": process_ref.recovery_outcome,
                "reconnectable": process_ref.reconnectable,
            },
        }
    )
    patch: dict[str, Any] = {
        "status": status,
        "command": process_ref.display_command,
        "metadata": metadata,
    }
    if process_ref.status is not NativeProcessStatus.RUNNING:
        patch["finished_at"] = process_ref.updated_at
    if status is RunStatus.FAILED:
        if process_ref.recovery_outcome is not None:
            patch["error"] = (
                f"Native process could not be recovered: {process_ref.recovery_outcome}"
            )
        else:
            patch["error"] = (
                f"Native process exited with code {process_ref.exit_code}"
                if process_ref.exit_code is not None
                else "Native process failed"
            )
    try:
        run = store.update_run(process_ref.run_id, **patch)
    except RunNotFoundError:
        return None
    if run.status is RunStatus.FAILED:
        _ensure_native_process_error_message(store, run, process_ref)
    if native_registry is not None:
        run = _sync_native_process_transcript(
            store,
            native_registry,
            run,
            process_ref,
            native_index_store=native_index_store,
        )
    return run


def _sync_native_process_transcript(
    store: HarnessSessionStore,
    native_registry: NativeHistoryConnectorRegistry,
    run: HarnessRun,
    process_ref: NativeProcessRef,
    *,
    native_index_store: NativeSessionIndexStore | None = None,
) -> HarnessRun:
    if run.harness_id not in {"codex-cli", "claude-code", "gemini-cli"}:
        return run
    snapshot = execution_snapshot_from_dict(
        _metadata_mapping(run.metadata.get("execution_snapshot"))
    )
    if snapshot is None:
        return run
    workspace = snapshot.source_workspace or snapshot.workspace or run.workspace
    discovery = native_registry.discover(
        harness_id=run.harness_id, workspace=workspace, include_external=False
    )
    candidates = [
        ref
        for ref in discovery.sessions
        if ref.execution_snapshot is not None
        and ref.execution_snapshot.id == snapshot.id
    ]
    if len(candidates) != 1:
        return run
    ref = candidates[0]
    title_updated = apply_provider_native_title(
        store,
        run.session_id,
        title=ref.title,
        run_id=run.id,
        provider=ref.harness_id,
        source_id=ref.native_session_id or ref.id,
    )
    if title_updated is not None:
        store.append_event(
            HarnessStoredEvent(
                id=new_id("evt"),
                session_id=run.session_id,
                run_id=run.id,
                type=HarnessEventType.SESSION_UPDATED.value,
                message="Session title revision stored.",
                payload={
                    "session_id": run.session_id,
                    "revision": title_updated.updated_at,
                    "changed_fields": ["title"],
                    "title": title_diagnostics(title_updated),
                },
                created_at=utc_now(),
            )
        )
    if native_index_store is not None:
        ref = native_index_store.upsert_ref(
            ref,
            project_id=_optional_text(ref.metadata.get("project_id"))
            or (
                ref.execution_snapshot.project_id
                if ref.execution_snapshot is not None
                else None
            ),
        )
        _append_reconciled_native_link(store, run, ref)
    if run.native_session_id != ref.native_session_id:
        run = store.update_run(
            run.id,
            native_session_id=ref.native_session_id,
            metadata={
                **dict(run.metadata),
                "native_history_reconciliation": {
                    "native_ref_id": ref.id,
                    "native_session_id": ref.native_session_id,
                    "updated_at": utc_now(),
                },
            },
        )
    if run.harness_id == "codex-cli":
        return run
    try:
        connector = native_registry.get(run.harness_id)
        transcript = connector.import_ref(ref)
    except (OSError, ValueError, UnknownNativeHistoryConnectorError):
        return run
    existing_messages = store.list_messages(run.session_id)
    known_keys = {
        str(message.metadata["native_message_key"])
        for message in existing_messages
        if message.run_id == run.id and message.metadata.get("native_message_key")
    }
    known_event_keys = {
        str(event.payload["native_event_key"])
        for event in store.list_events(run.session_id, run_id=run.id)
        if event.payload.get("native_event_key")
    }
    appended_message_count = 0
    appended_event_count = 0
    for message in transcript:
        if not _native_message_belongs_to_run(message, run):
            continue
        message_key = _native_message_key(ref, message)
        for tool_call in _native_tool_records(message.metadata.get("tool_calls")):
            event_key = _native_tool_event_key(
                message_key, HarnessEventType.TOOL_CALL_STARTED.value, tool_call
            )
            if event_key in known_event_keys:
                continue
            store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type=HarnessEventType.TOOL_CALL_STARTED.value,
                    message="Native tool call started.",
                    payload={
                        **tool_call,
                        "native_event_key": event_key,
                        "native_message_key": message_key,
                        "native_ref_id": ref.id,
                        "native_session_id": ref.native_session_id,
                    },
                    created_at=message.created_at or utc_now(),
                )
            )
            known_event_keys.add(event_key)
            appended_event_count += 1
        for tool_result in _native_tool_records(message.metadata.get("tool_results")):
            event_key = _native_tool_event_key(
                message_key, HarnessEventType.TOOL_CALL_FINISHED.value, tool_result
            )
            if event_key in known_event_keys:
                continue
            store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type=HarnessEventType.TOOL_CALL_FINISHED.value,
                    message="Native tool call finished.",
                    payload={
                        **tool_result,
                        "native_event_key": event_key,
                        "native_message_key": message_key,
                        "native_ref_id": ref.id,
                        "native_session_id": ref.native_session_id,
                    },
                    created_at=message.created_at or utc_now(),
                )
            )
            known_event_keys.add(event_key)
            appended_event_count += 1
        if (
            _native_import_message_role(message.role) != "assistant"
            or not message.content.strip()
            or message_key in known_keys
        ):
            continue
        store.append_message(
            HarnessMessage(
                id=new_id("msg"),
                session_id=run.session_id,
                run_id=run.id,
                role="assistant",
                content=str(redact_for_storage(message.content)),
                created_at=message.created_at or utc_now(),
                harness_id=run.harness_id,
                model=run.model,
                api_mode=run.api_mode,
                metadata={
                    "source": "native_process",
                    "process_id": process_ref.id,
                    "native_ref_id": ref.id,
                    "native_session_id": ref.native_session_id,
                    "native_message_key": message_key,
                    "native_metadata": _redacted_mapping(message.metadata),
                },
            )
        )
        store.append_event(
            HarnessStoredEvent(
                id=new_id("evt"),
                session_id=run.session_id,
                run_id=run.id,
                type=HarnessEventType.MESSAGE_COMPLETED.value,
                message="Native assistant message synchronized.",
                payload={
                    "role": "assistant",
                    "process_id": process_ref.id,
                    "native_ref_id": ref.id,
                    "native_session_id": ref.native_session_id,
                },
                created_at=message.created_at or utc_now(),
            )
        )
        known_keys.add(message_key)
        appended_message_count += 1
    if (
        not appended_message_count
        and (not appended_event_count)
        and (run.native_session_id == ref.native_session_id)
    ):
        return run
    metadata = {
        **dict(run.metadata),
        "native_transcript_sync": {
            "native_ref_id": ref.id,
            "native_session_id": ref.native_session_id,
            "synced_message_count": len(known_keys),
            "synced_tool_event_count": len(known_event_keys),
            "updated_at": utc_now(),
        },
    }
    return store.update_run(
        run.id, native_session_id=ref.native_session_id, metadata=metadata
    )


def _append_reconciled_native_link(
    store: HarnessSessionStore, run: HarnessRun, ref: NativeSessionRef
) -> None:
    current = store.get_native_link(run.session_id, run.harness_id)
    if current is not None and current.native_ref_id == ref.id:
        return
    now = utc_now()
    metadata = dict(current.metadata) if current is not None else {}
    metadata.update(
        {
            "auto_reconciled": True,
            "project_id": ref.metadata.get("project_id"),
            "execution_snapshot": execution_snapshot_to_dict(ref.execution_snapshot)
            if ref.execution_snapshot is not None
            else None,
        }
    )
    store.append_native_link(
        run.session_id,
        HarnessNativeLink(
            id=new_id("nlink"),
            session_id=run.session_id,
            harness_id=run.harness_id,
            status=ref.status,
            created_at=current.created_at if current is not None else now,
            updated_at=now,
            native_session_id=ref.native_session_id,
            native_ref_id=ref.id,
            source="native_history_reconciliation",
            workspace=ref.workspace or run.workspace,
            metadata=metadata,
        ),
    )


def _native_message_belongs_to_run(
    message: NativeTranscriptMessage, run: HarnessRun
) -> bool:
    if run.started_at is None:
        return True
    message_time = _parse_native_timestamp(message.created_at)
    run_time = _parse_native_timestamp(run.started_at)
    return (
        message_time is not None and run_time is not None and (message_time >= run_time)
    )


def _parse_native_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _native_message_key(ref: NativeSessionRef, message: NativeTranscriptMessage) -> str:
    native_message_id = _optional_text(message.metadata.get("native_message_id"))
    if native_message_id is not None:
        return f"{ref.id}:id:{native_message_id}"
    digest = hashlib.sha256(
        json.dumps(
            [
                message.role,
                message.created_at,
                message.content,
                message.metadata.get("tool_calls"),
                message.metadata.get("tool_results"),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return f"{ref.id}:sha256:{digest}"


def _native_run_messages(
    store: HarnessSessionStore, run: HarnessRun
) -> tuple[HarnessMessage, ...]:
    return tuple(
        (
            message
            for message in store.list_messages(run.session_id)
            if message.run_id == run.id and message.role in {"assistant", "error"}
        )
    )


def _native_run_events(
    store: HarnessSessionStore, run: HarnessRun
) -> tuple[HarnessStoredEvent, ...]:
    return store.list_events(run.session_id, run_id=run.id)


def _native_tool_records(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple((dict(item) for item in value if isinstance(item, Mapping)))


def _native_tool_event_key(
    message_key: str, event_type: str, payload: Mapping[str, Any]
) -> str:
    tool_call_id = _optional_text(payload.get("tool_call_id")) or "tool-call"
    return f"{message_key}:{event_type}:{tool_call_id}"
