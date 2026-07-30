"""Bounded session reads shared by in-process TUI navigation."""

from __future__ import annotations

from gpt2giga_harness.sessions.api import SessionQueryStore
from gpt2giga_harness.sessions.models import HarnessMessage, HarnessRun
from gpt2giga_harness.tui.contracts import _TERMINAL_RUN_STATUSES

MAX_TUI_RUN_QUERY_LIMIT = 100
MAX_TUI_TRANSCRIPT_MESSAGES = 100
_TRUNCATED_RUN_HISTORY_LEASE = "bounded-run-history"


def session_navigation_records(
    store: SessionQueryStore,
    session_id: str,
) -> tuple[HarnessMessage | None, str | None]:
    """Return one preview message and a fail-closed bounded activity lease."""
    messages = store.list_recent_messages(session_id, limit=1)
    page = store.list_runs_page(session_id, limit=MAX_TUI_RUN_QUERY_LIMIT)
    active = next(
        (run for run in page.items if run.status.value not in _TERMINAL_RUN_STATUSES),
        None,
    )
    if active is not None:
        lease = active.id
    elif page.has_more:
        lease = _TRUNCATED_RUN_HISTORY_LEASE
    else:
        lease = None
    return (messages[-1] if messages else None), lease


def session_preview_messages(
    store: SessionQueryStore,
    session_id: str,
    query: str,
) -> tuple[tuple[HarnessMessage, ...], int, bool]:
    """Search one recent bounded transcript window for a TUI preview."""
    messages = store.list_recent_messages(
        session_id,
        limit=MAX_TUI_TRANSCRIPT_MESSAGES + 1,
    )
    truncated = len(messages) > MAX_TUI_TRANSCRIPT_MESSAGES
    recent = messages[-MAX_TUI_TRANSCRIPT_MESSAGES:]
    needle = query.strip().casefold()
    matches = tuple(
        message
        for message in recent
        if not needle or needle in message.content.casefold()
    )
    return matches, len(matches), truncated


def preferred_session_run(
    store: SessionQueryStore,
    session_id: str,
) -> HarnessRun | None:
    """Return the newest active run, otherwise the newest retained run."""
    page = store.list_runs_page(session_id, limit=MAX_TUI_RUN_QUERY_LIMIT)
    active = next(
        (run for run in page.items if run.status.value not in _TERMINAL_RUN_STATUSES),
        None,
    )
    return active or (page.items[0] if page.items else None)


def newest_session_run_id(
    store: SessionQueryStore,
    session_id: str,
) -> str | None:
    """Return the newest run identity with one indexed row read."""
    page = store.list_runs_page(session_id, limit=1)
    return page.items[0].id if page.items else None
