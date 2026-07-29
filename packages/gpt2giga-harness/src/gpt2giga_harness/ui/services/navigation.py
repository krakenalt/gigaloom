"""Session navigation projections and mutation bindings."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import re
from typing import Any, Mapping

from fastapi import HTTPException

from gpt2giga_harness.runtime.models import RunStatus
from gpt2giga_harness.session_titles import manual_title_metadata, title_diagnostics
from gpt2giga_harness.sessions import (
    HarnessSessionStore,
    SessionNotFoundError,
)
from gpt2giga_harness.sessions.models import (
    HarnessMessage,
    HarnessSession,
    session_to_dict,
)
from gpt2giga_harness.sessions.store import new_id, utc_now
from gpt2giga_harness.types import parse_api_mode
from gpt2giga_harness.workspace import resolve_workspace

_FILE_PREVIEW_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_NAVIGATION_TERMINAL_RE = re.compile(
    r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)?|P.*?(?:\x1b\\|$)|"
    r"\[[0-?]*[ -/]*[@-~]|[@-_])",
    re.DOTALL,
)
_NAVIGATION_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def session_summary(
    store: HarnessSessionStore,
    session_id: str,
) -> dict[str, Any]:
    """Build the bounded session navigation projection."""
    session = store.get_session(session_id)
    messages = store.list_messages(session_id)
    runs = store.list_runs(session_id)
    preview = ""
    if messages:
        preview = " ".join(messages[-1].content.split())[:120]
    last_status = runs[-1].status if runs else None
    active_runs = [
        run.id
        for run in runs
        if run.status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED}
    ]
    native_reference = navigation_native_reference(session)
    payload = session_to_dict(session)
    project_id = _optional_text(session.metadata.get("project_id"))
    payload.update(
        {
            "last_message_preview": preview,
            "last_run_status": last_status,
            "project_id": project_id,
            "project": (
                {
                    "id": project_id,
                    "root": session.metadata.get("project_root"),
                    "name": session.metadata.get("project_name"),
                }
                if project_id
                else None
            ),
            "native_session_reference": native_reference,
            "session_revision": session.updated_at,
            "session_generation": navigation_generation(native_reference),
            "session_lease": active_runs[-1] if active_runs else None,
            "title_diagnostics": title_diagnostics(session),
        }
    )
    return payload


def navigation_native_reference(session: HarnessSession) -> dict[str, Any]:
    """Resolve the authoritative native reference for navigation."""
    explicit = session.metadata.get("native_session_reference")
    if isinstance(explicit, Mapping):
        return dict(explicit)
    structured = session.metadata.get("structured_session_link")
    if isinstance(structured, Mapping):
        return {
            "authority": structured.get("provider")
            or structured.get("authority")
            or session.native.get("harness_id"),
            "native_id": structured.get("thread_id")
            or structured.get("session_id")
            or structured.get("native_session_id"),
            "operation": structured.get("operation") or "resume",
            "revision": structured.get("revision"),
            "link_hash": structured.get("link_hash"),
        }
    return dict(session.native)


def navigation_generation(reference: Mapping[str, Any]) -> int:
    """Derive the non-negative generation bound to a native reference."""
    revision = reference.get("revision")
    if isinstance(revision, int) and revision >= 0:
        return revision
    link_hash = _optional_text(reference.get("link_hash"))
    if not link_hash:
        return 0
    return int(hashlib.sha256(link_hash.encode("utf-8")).hexdigest()[:8], 16)


def validate_navigation_binding(
    store: HarnessSessionStore,
    session_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate optimistic session revision, generation, and lease binding."""
    try:
        summary = session_summary(store, session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    expected = (
        _required_text(payload.get("session_revision"), "session_revision"),
        _navigation_non_negative_int(
            payload.get("session_generation"), "session_generation"
        ),
        _optional_text(payload.get("session_lease")),
    )
    current = (
        summary["session_revision"],
        summary["session_generation"],
        summary["session_lease"],
    )
    if expected != current:
        raise HTTPException(
            status_code=409,
            detail="Session changed; authoritative resnapshot required",
        )
    _required_text(payload.get("idempotency_key"), "idempotency_key")
    return summary


def navigation_cached(
    cache: Mapping[str, dict[str, Any]],
    payload: Mapping[str, Any],
    action: str,
) -> dict[str, Any] | None:
    """Return a cached mutation response by action and idempotency key."""
    key = _optional_text(payload.get("idempotency_key"))
    return cache.get(f"{action}:{key}") if key else None


def cache_navigation_response(
    cache: dict[str, dict[str, Any]],
    payload: Mapping[str, Any],
    response: dict[str, Any],
    action: str,
) -> None:
    """Cache a bounded navigation response by idempotency key."""
    key = _required_text(payload.get("idempotency_key"), "idempotency_key")
    if len(cache) >= 512:
        cache.pop(next(iter(cache)))
    cache[f"{action}:{key}"] = response


def navigation_message_preview(message: HarnessMessage) -> str:
    """Render a control-safe bounded transcript preview."""
    content = navigation_safe_text(message.content)[:512]
    return f"{navigation_safe_text(message.role).upper()} · {message.created_at}\n{content}"


def navigation_export_text(
    session: HarnessSession,
    messages: tuple[HarnessMessage, ...],
) -> str:
    """Render a control-safe conversation export."""
    transcript = "\n\n".join(
        f"## {navigation_safe_text(item.role).title()} · {item.created_at}\n\n"
        f"{navigation_safe_text(item.content)}"
        for item in messages
    )
    return (
        f"# {navigation_safe_text(session.title)}\n\n"
        f"Session: `{session.id}`  \n"
        "Workspace: conversation context only; filesystem restore is not included.\n\n"
        f"{transcript}\n"
    )


def navigation_safe_text(value: Any) -> str:
    """Strip terminal and bidirectional controls from exported UI text."""
    text = _NAVIGATION_TERMINAL_RE.sub("⟦terminal-control⟧", str(value))
    text = _FILE_PREVIEW_CONTROL_RE.sub("�", text)
    return _NAVIGATION_BIDI_RE.sub("�", text)


def fork_session_from_session(
    store: HarnessSessionStore,
    session_id: str,
) -> HarnessSession:
    """Fork one session without retaining native resume authority."""
    source = store.get_session(session_id)
    reference = navigation_native_reference(source)
    metadata = {
        **dict(source.metadata),
        "forked_from_session_id": source.id,
        "fork_semantics": "harness_replay",
    }
    metadata.pop("structured_session_link", None)
    if reference:
        metadata["native_session_reference"] = {
            "authority": reference.get("authority"),
            "native_id": reference.get("native_id")
            or reference.get("session_id")
            or reference.get("id"),
            "workspace": source.workspace,
            "operation": "fork",
        }
    fork = store.create_session(
        title=f"Fork: {source.title}",
        workspace=source.workspace,
        default_harness_id=source.default_harness_id,
        default_model=source.default_model,
        default_api_mode=source.default_api_mode,
        default_mode=source.default_mode,
        metadata=metadata,
    )
    for message in store.list_messages(source.id):
        store.append_message(
            replace(
                message,
                id=new_id("msg"),
                session_id=fork.id,
                run_id=None,
                created_at=utc_now(),
                metadata={
                    **dict(message.metadata),
                    "forked_from_message_id": message.id,
                },
            )
        )
    return fork


def session_patch(
    payload: dict[str, Any],
    *,
    session: HarnessSession | None = None,
) -> dict[str, Any]:
    """Build the behavior-compatible mutable session patch."""
    allowed = {
        "title",
        "workspace",
        "default_harness_id",
        "default_model",
        "default_api_mode",
        "default_mode",
        "pinned",
        "archived",
        "tags",
        "metadata",
    }
    patch = {key: payload[key] for key in allowed if key in payload}
    if "title" in patch and isinstance(patch.get("metadata"), Mapping):
        patch["metadata"] = manual_title_metadata(patch["metadata"])
    if "workspace" in patch:
        patch["workspace"] = resolve_workspace(_optional_text(patch["workspace"]))
    if "default_api_mode" in patch:
        patch["default_api_mode"] = parse_api_mode(patch["default_api_mode"])
    if "workbench_selection" in payload:
        if session is None:
            raise ValueError("session is required for workbench selection")
        selection = payload["workbench_selection"]
        if not isinstance(selection, Mapping):
            raise ValueError("workbench selection must be an object")
        kind = str(selection.get("kind") or "")
        intent = str(selection.get("intent") or "")
        authority = str(selection.get("authority") or "")
        if kind not in {"coding_agent", "direct_chat"}:
            raise ValueError("workbench kind is invalid")
        if intent not in {"ask", "review", "change"}:
            raise ValueError("task intent is invalid")
        if authority not in {"read_only", "workspace_write"}:
            raise ValueError("authority is invalid")
        patch["default_mode"] = (
            "plan"
            if intent == "ask"
            else "read"
            if intent == "review" or authority == "read_only"
            else "edit"
        )
        patch["metadata"] = {
            **dict(session.metadata),
            "workbench_selection": {
                "schema_version": 1,
                "kind": kind,
                "intent": intent,
                "authority": authority,
                "input_source": "product",
                "compatibility_warning": None,
            },
        }
    return patch


def _navigation_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"{field_name} must be an integer"
        ) from exc
    if result < 0:
        raise HTTPException(
            status_code=400, detail=f"{field_name} must be non-negative"
        )
    return result


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: Any, message: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(message)
    return text
