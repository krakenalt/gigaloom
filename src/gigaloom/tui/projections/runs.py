"""Typed runs projections for TUI clients."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessStoredEvent,
    run_to_dict,
)

from gigaloom.tui.contracts import (
    MAX_TIMELINE_EVENTS,
    MAX_TIMELINE_CHARS,
    WorkbenchClientError,
    RunActionBinding,
    TimelineEvent,
    ApprovalSummary,
    RunSnapshot,
)

from gigaloom.tui.projections.values import (
    _safe_paths,
    _mapping,
    _optional_text,
    _display_text,
    _optional_display_text,
    _required_identity,
    _hash_generation,
    _bounded_event_text,
    _optional_identity,
)


def _run_revision(run: HarnessRun) -> str:
    payload = json.dumps(
        run_to_dict(run),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mapping_revision(run: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(run),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_generation(run: HarnessRun) -> int:
    metadata = _mapping(run.metadata)
    link = _mapping(metadata.get("structured_session_link"))
    revision = link.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0:
        return revision
    runtime = _mapping(metadata.get("runtime"))
    attempt = runtime.get("attempt_number")
    if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt > 0:
        return attempt
    link_hash = _optional_text(link.get("link_hash"))
    return _hash_generation(link_hash) if link_hash else 1


def _mapping_generation(provider_session: Mapping[str, Any]) -> int:
    revision = provider_session.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0:
        return revision
    link_hash = _optional_text(provider_session.get("link_hash"))
    return _hash_generation(link_hash) if link_hash else 1


def _run_idempotency_key(run: HarnessRun, submitted: Mapping[str, str]) -> str:
    return next(
        (key for key, run_id in submitted.items() if run_id == run.id),
        f"run-{run.id}",
    )


def _run_snapshot(
    run: HarnessRun,
    *,
    events: tuple[TimelineEvent, ...],
    cursor: str | None,
    idempotency_key: str,
    pending_approvals: tuple[ApprovalSummary, ...] = (),
    resnapshot_reason: str | None = None,
) -> RunSnapshot:
    metadata = _mapping(run.metadata)
    native_process = _mapping(metadata.get("native_process"))
    return RunSnapshot(
        binding=RunActionBinding(
            session_id=run.session_id,
            run_id=run.id,
            revision=_run_revision(run),
            generation=_run_generation(run),
            idempotency_key=_required_identity(idempotency_key, "run idempotency key"),
        ),
        status=run.status.value,
        events=events,
        cursor=cursor,
        pending_approvals=pending_approvals,
        resnapshot_reason=resnapshot_reason,
        execution_transport=_optional_display_text(metadata.get("execution_transport")),
        native_process_id=_optional_text(native_process.get("id")),
    )


def _parse_in_process_cursor(
    value: str | None,
) -> tuple[int, int | None, bool]:
    if value is None:
        return 0, None, False
    parts = value.split(".", 2)
    if len(parts) != 3 or parts[0] != "ip1":
        return 0, None, True
    try:
        generation = int(parts[1])
        offset = int(parts[2])
    except ValueError:
        return 0, None, True
    if generation < 1 or offset < 0:
        return 0, None, True
    return offset, generation, False


def _parse_attach_cursor(
    value: str | None,
) -> tuple[str | None, int | None, bool]:
    if value is None:
        return None, None, False
    parts = value.split(".", 2)
    if len(parts) != 3 or parts[0] != "at1":
        return None, None, True
    try:
        generation = int(parts[1])
    except ValueError:
        return None, None, True
    try:
        event_id = _required_identity(parts[2], "event cursor")
    except WorkbenchClientError:
        return None, None, True
    return event_id, generation, False


def _bounded_timeline(
    events: tuple[HarnessStoredEvent, ...],
) -> tuple[TimelineEvent, ...]:
    retained: list[TimelineEvent] = []
    character_count = 0
    for event in reversed(events[-MAX_TIMELINE_EVENTS:]):
        item = _timeline_event(event)
        item_size = (
            len(item.message) + len(item.delta or "") + len(item.tool_name or "")
        )
        if retained and character_count + item_size > MAX_TIMELINE_CHARS:
            break
        retained.append(item)
        character_count += item_size
    retained.reverse()
    return tuple(retained)


def _timeline_event(event: HarnessStoredEvent) -> TimelineEvent:
    payload = _mapping(event.payload)
    delta = None
    for key in ("delta", "text", "content", "reasoning"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            delta = _bounded_event_text(value)
            break
    return TimelineEvent(
        id=_required_identity(event.id, "event id"),
        type=_display_text(event.type),
        message=_bounded_event_text(event.message),
        delta=delta,
        tool_name=_optional_display_text(
            payload.get("name") or payload.get("tool_name")
        ),
        approval_id=_optional_identity(payload.get("approval_id")),
        input_id=_optional_identity(
            payload.get("input_id") or payload.get("request_id")
        ),
        category=_timeline_category(event.type),
        stream=_optional_display_text(payload.get("stream")),
        artifact_id=_optional_identity(
            payload.get("artifact_id") or payload.get("file_id")
        ),
        artifact_kind=_optional_display_text(
            payload.get("artifact_type") or payload.get("kind")
        ),
        truncated=bool(payload.get("truncated")),
    )


def _event_from_mapping(value: Mapping[str, Any]) -> HarnessStoredEvent:
    return HarnessStoredEvent(
        id=_required_identity(value.get("id"), "event id"),
        session_id=_required_identity(value.get("session_id"), "session id"),
        run_id=_required_identity(value.get("run_id"), "run id"),
        type=_display_text(value.get("type") or "event"),
        message=_bounded_event_text(value.get("message") or ""),
        payload=_mapping(value.get("payload")),
        created_at=_display_text(value.get("created_at") or "unknown"),
    )


def _approval_summary(value: Mapping[str, Any]) -> ApprovalSummary:
    preview = _mapping(value.get("preview"))
    executable = preview.get("executable") or preview.get("command")
    if isinstance(executable, (list, tuple)):
        executable = executable[0] if executable else None
    paths = _safe_paths(preview.get("paths") or preview.get("changed_files"))
    hash_bound = bool(preview.get("approval_binding_sha256"))
    scopes = (
        ("allow_once", "deny")
        if hash_bound or not value.get("run_id")
        else ("allow_once", "allow_run", "deny")
    )
    details: list[str] = []
    author = preview.get("author")
    if isinstance(author, Mapping):
        details.append(
            "Author: "
            + _display_text(author.get("name") or "unknown")
            + " <"
            + _display_text(author.get("email") or "unknown")
            + ">"
        )
    if preview.get("message") is not None:
        details.append("Message: " + _display_text(preview.get("message")))
    if preview.get("head") is not None:
        details.append("HEAD: " + _display_text(preview.get("head")))
    if preview.get("diff_sha256") is not None:
        details.append("Diff: " + _display_text(preview.get("diff_sha256")))
    if preview.get("remote") is not None:
        details.append("Remote: " + _display_text(preview.get("remote")))
    if preview.get("upstream") is not None:
        details.append("Upstream: " + _display_text(preview.get("upstream")))
    if preview.get("target_branch") is not None:
        details.append("Target: " + _display_text(preview.get("target_branch")))
    repository = preview.get("repository")
    if isinstance(repository, Mapping):
        details.append(
            "Repository: "
            + _display_text(repository.get("name_with_owner") or "unavailable")
        )
    if preview.get("source_branch") is not None:
        details.append(
            "Source: "
            + _display_text(preview.get("source_branch"))
            + " @ "
            + _display_text(preview.get("source_head"))
        )
    if preview.get("base_branch") is not None:
        details.append(
            "Base: "
            + _display_text(preview.get("base_branch"))
            + " @ "
            + _display_text(preview.get("base_head"))
        )
    if preview.get("title") is not None:
        details.append("Title: " + _display_text(preview.get("title")))
    if preview.get("body") is not None:
        details.append("Body: " + _display_text(preview.get("body")))
    permissions = preview.get("permissions")
    if isinstance(permissions, Mapping):
        granted = sorted(
            str(key) for key, enabled in permissions.items() if enabled is True
        )
        blocked = sorted(
            str(key) for key, enabled in permissions.items() if enabled is False
        )
        if granted:
            details.append("Permits: " + ", ".join(granted))
        if blocked:
            details.append("Forbids: " + ", ".join(blocked))
    return ApprovalSummary(
        id=_required_identity(value.get("id"), "approval id"),
        action=_display_text(value.get("action") or "approval"),
        reason=_display_text(value.get("reason") or "Approval required"),
        status=_display_text(value.get("status") or "pending"),
        enforcement=_display_text(value.get("enforcement") or "unknown"),
        enforcement_owner=_display_text(value.get("enforcement_owner") or "unknown"),
        policy_source=_display_text(value.get("policy_source") or "unknown"),
        executable=_display_text(executable or "not declared"),
        tool=_display_text(
            preview.get("tool") or preview.get("tool_name") or "not declared"
        ),
        cwd=_display_text(preview.get("cwd") or "not declared"),
        paths=paths,
        network=_approval_network_label(preview),
        mutation_class=_display_text(
            preview.get("mutation_class") or value.get("action") or "unknown"
        ),
        decision_scopes=scopes,
        details=tuple(details),
    )


def _timeline_category(event_type: str) -> str:
    normalized = event_type.lower().replace("-", "_")
    if "approval" in normalized:
        return "approval"
    if "question" in normalized or "input_request" in normalized:
        return "question"
    if "reason" in normalized:
        return "reasoning"
    if "stderr" in normalized:
        return "stderr"
    if "stdout" in normalized or "output_delta" in normalized:
        return "stdout"
    if "diff" in normalized:
        return "diff"
    if "file" in normalized or "attachment" in normalized:
        return "file"
    if "mcp" in normalized:
        return "mcp"
    if "web" in normalized:
        return "web"
    if "tool" in normalized or "command" in normalized:
        return "tool"
    if "plan" in normalized or "todo" in normalized:
        return "plan"
    if "warning" in normalized:
        return "warning"
    if "error" in normalized or "failed" in normalized:
        return "error"
    if "message" in normalized or normalized in {"user", "assistant", "agent"}:
        return "message"
    return "status"


def _approval_network_label(preview: Mapping[str, Any]) -> str:
    value = preview.get("network")
    if isinstance(value, bool):
        return "required" if value else "not required"
    if value is None:
        value = preview.get("network_required")
    if isinstance(value, bool):
        return "required" if value else "not required"
    return _display_text(value or "not declared")


def _binding_payload(binding: RunActionBinding) -> dict[str, Any]:
    return {
        "session_id": binding.session_id,
        "run_id": binding.run_id,
        "revision": binding.revision,
        "generation": binding.generation,
        "idempotency_key": binding.idempotency_key,
    }


def _messages_through_run(
    messages: tuple[HarnessMessage, ...], run_id: str
) -> tuple[HarnessMessage, ...]:
    selected: list[HarnessMessage] = []
    seen_target = False
    for message in messages:
        selected.append(message)
        if message.run_id == run_id:
            seen_target = True
            if message.role in {"assistant", "error"}:
                break
        elif seen_target:
            selected.pop()
            break
    return tuple(selected)
