"""Arena and evaluation projections owned by the UI application layer."""

from __future__ import annotations

from typing import Any

from gigaloom.arena import (
    HarnessArenaChildRun,
    HarnessArenaRun,
    arena_child_to_dict,
    arena_review_projection,
    arena_to_dict,
)
from gigaloom.evals import HarnessEvalRun, eval_run_to_dict
from gigaloom.sessions import (
    HarnessSessionStore,
    RunNotFoundError,
    SessionNotFoundError,
)
from gigaloom.sessions.models import (
    HarnessRun,
    event_to_dict,
    message_to_dict,
    run_to_dict,
)
from gigaloom.ui.services.navigation import session_summary
from gigaloom.ui.services.session_queries import (
    recent_events,
    recent_messages,
    recent_runs,
)


def arena_response(
    arena: HarnessArenaRun,
    store: HarnessSessionStore,
) -> dict[str, Any]:
    """Build the bounded interactive response for an Arena run."""
    payload = arena_to_dict(arena)
    payload["child_runs"] = [
        arena_child_response(child, store) for child in arena.child_runs
    ]
    payload["review"] = arena_review_projection(arena, store)
    try:
        payload["session"] = session_summary(store, arena.session_id)
    except SessionNotFoundError:
        payload["session"] = None
    return {"arena": payload}


def arena_summary_response(arena: HarnessArenaRun) -> dict[str, Any]:
    """Build the content-minimized Arena list projection."""
    payload = arena_to_dict(arena)
    payload["prompt"] = ""
    payload["child_runs"] = [arena_child_to_dict(child) for child in arena.child_runs]
    return payload


def eval_run_response(
    eval_run: HarnessEvalRun,
    store: HarnessSessionStore,
) -> dict[str, Any]:
    """Build an evaluation response with its optional session projection."""
    payload = eval_run_to_dict(eval_run)
    try:
        payload["session"] = session_summary(store, eval_run.session_id)
    except SessionNotFoundError:
        payload["session"] = None
    return {"eval_run": payload}


def arena_child_response(
    child: HarnessArenaChildRun,
    store: HarnessSessionStore,
) -> dict[str, Any]:
    """Build one bounded child-run projection."""
    payload = arena_child_to_dict(child)
    if child.run_id is None:
        return payload
    try:
        run = store.get_run(child.run_id)
        payload["run"] = run_to_dict(run)
        payload["message"] = _last_run_message(store, run)
        messages = recent_messages(store, run.session_id, limit=100)
        runs = recent_runs(store, run.session_id, limit=50)
        events = recent_events(store, run.session_id, limit=200)
        payload["messages"] = [message_to_dict(item) for item in messages]
        payload["runs"] = [run_to_dict(item) for item in runs]
        payload["activity"] = [
            event_to_dict(item)
            for item in events
            if item.type.startswith(("tool_", "approval_"))
            or item.type
            in {
                "cancel_requested",
                "error",
                "run_canceled",
                "run_finished",
                "warning",
            }
        ][-100:]
        payload["event_count"] = len(events)
        payload["bounded"] = True
    except (RunNotFoundError, SessionNotFoundError):
        payload["missing"] = True
    return payload


def bounded_arena_workspace_paths(value: Any) -> tuple[str, ...]:
    """Validate and bound Arena workspace-file inputs."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("workspace_paths must be a list")
    if len(value) > 8:
        raise ValueError("workspace_paths must contain at most 8 files")
    paths = tuple(str(item).strip() for item in value)
    if any(not path for path in paths):
        raise ValueError("workspace_paths must contain non-empty strings")
    return paths


def first_text(value: Any) -> str | None:
    """Return the first non-empty item from a list-like payload."""
    if not isinstance(value, list) or not value:
        return None
    text = str(value[0]).strip()
    return text or None


def _last_run_message(
    store: HarnessSessionStore,
    run: HarnessRun,
) -> dict[str, Any] | None:
    messages = [
        message
        for message in recent_messages(store, run.session_id, limit=100)
        if message.run_id == run.id and message.role in {"assistant", "error"}
    ]
    if not messages:
        return None
    return message_to_dict(messages[-1])
