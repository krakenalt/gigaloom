"""Bounded session-query helpers for interactive UI projections."""

from __future__ import annotations

from collections import deque
from collections.abc import Collection

from gigaloom.sessions import (
    EventNotFoundError,
    FilesystemHarnessSessionStore,
    HarnessMessage,
    HarnessRawRecord,
    HarnessRun,
    HarnessSession,
    HarnessSessionStore,
    HarnessStoredEvent,
    MessageNotFoundError,
)
from gigaloom.sessions.read_index import SessionIndexCursor

MAX_UI_MESSAGES = 1_000
MAX_UI_RECORDS = 100
MAX_UI_SESSION_PAGE = 200
MAX_UI_EVENT_BYTES = 1024 * 1024


def list_session_window(
    store: HarnessSessionStore,
    *,
    project_id: str | None,
    workspace: str | None,
    harness_id: str | None,
    q: str | None,
    include_archived: bool,
    limit: int,
    excluded_ids: Collection[str] = (),
) -> tuple[HarnessSession, ...]:
    """Return at most ``limit`` matching non-excluded session summaries."""
    excluded = frozenset(excluded_ids)
    if not isinstance(store, FilesystemHarnessSessionStore):
        requested = limit + len(excluded)
        return tuple(
            session
            for session in store.list_sessions(
                project_id=project_id,
                workspace=workspace,
                harness_id=harness_id,
                q=q,
                include_archived=include_archived,
                limit=requested,
            )
            if session.id not in excluded
        )[:limit]

    selected: list[HarnessSession] = []
    cursor: SessionIndexCursor | None = None
    while len(selected) < limit:
        page = store.list_sessions_page(
            project_id=project_id,
            workspace=workspace,
            harness_id=harness_id,
            q=q,
            include_archived=include_archived,
            cursor=cursor,
            limit=MAX_UI_SESSION_PAGE,
        )
        selected.extend(item for item in page.items if item.id not in excluded)
        if not page.has_more or not page.items:
            break
        last = page.items[-1]
        cursor = SessionIndexCursor(
            generation=page.generation,
            pinned=int(last.pinned),
            updated_at=last.updated_at,
            session_id=last.id,
        )
    return tuple(selected[:limit])


def recent_messages(
    store: HarnessSessionStore,
    session_id: str,
    *,
    limit: int,
) -> tuple[HarnessMessage, ...]:
    """Return the latest active messages in chronological order."""
    return store.list_recent_messages(
        session_id,
        limit=min(max(limit, 1), MAX_UI_MESSAGES),
    )


def has_older_messages(
    store: HarnessSessionStore,
    session_id: str,
    messages: tuple[HarnessMessage, ...],
) -> bool:
    """Report whether an active message window omitted older history."""
    if not messages:
        return False
    return bool(
        store.list_recent_messages(
            session_id,
            limit=1,
            before=messages[0].id,
        )
    )


def recent_runs(
    store: HarnessSessionStore,
    session_id: str,
    *,
    limit: int,
) -> tuple[HarnessRun, ...]:
    """Return the latest run window in legacy chronological order."""
    page = store.list_runs_page(
        session_id,
        limit=min(max(limit, 1), MAX_UI_RECORDS),
    )
    return tuple(reversed(page.items))


def recent_events(
    store: HarnessSessionStore,
    session_id: str,
    *,
    run_id: str | None = None,
    limit: int = MAX_UI_RECORDS,
) -> tuple[HarnessStoredEvent, ...]:
    """Return the latest bounded event window in append order."""
    retained: deque[HarnessStoredEvent] = deque(maxlen=max(limit, 1))
    offset = 0
    while True:
        page = store.list_events_page(
            session_id,
            run_id=run_id,
            offset=offset,
            limit=MAX_UI_RECORDS,
            max_bytes=MAX_UI_EVENT_BYTES,
        )
        retained.extend(item.event for item in page.items)
        if not page.has_more or page.next_offset <= offset:
            break
        offset = page.next_offset
    return tuple(retained)


def events_after(
    store: HarnessSessionStore,
    session_id: str,
    *,
    run_id: str | None,
    after_id: str | None,
    limit: int = MAX_UI_RECORDS,
) -> tuple[HarnessStoredEvent, ...]:
    """Return a bounded event poll window while preserving legacy cursors."""
    if after_id is None:
        return recent_events(store, session_id, run_id=run_id, limit=limit)
    position = store.resolve_event_cursor(
        session_id,
        run_id=run_id,
        event_id=after_id,
    )
    if position is None:
        return ()
    selected: list[HarnessStoredEvent] = []
    offset = position.offset
    while len(selected) < limit:
        page = store.list_events_page(
            session_id,
            run_id=run_id,
            offset=offset,
            limit=min(MAX_UI_RECORDS, limit - len(selected)),
            max_bytes=MAX_UI_EVENT_BYTES,
        )
        selected.extend(item.event for item in page.items)
        if not page.has_more or page.next_offset <= offset:
            break
        offset = page.next_offset
    return tuple(selected)


def message_for_session(
    store: HarnessSessionStore,
    session_id: str,
    message_id: str,
) -> HarnessMessage:
    """Resolve one retained message and enforce its session binding."""
    store.get_session(session_id)
    message = store.get_message(message_id)
    if message.session_id != session_id:
        raise MessageNotFoundError(message_id)
    return message


def event_for_run(
    store: HarnessSessionStore,
    session_id: str,
    run_ids: Collection[str],
    event_id: str,
) -> HarnessStoredEvent:
    """Resolve one retained event and enforce its run/session binding."""
    event = store.get_event(event_id)
    if event.session_id != session_id or event.run_id not in run_ids:
        raise EventNotFoundError(event_id)
    return event


def raw_requests_for_run(
    store: HarnessSessionStore,
    run_id: str,
    *,
    limit: int = MAX_UI_RECORDS,
) -> tuple[HarnessRawRecord, ...]:
    """Return a bounded chronological raw-request window for one run."""
    return store.list_raw_requests_for_run(
        run_id,
        limit=min(max(limit, 1), MAX_UI_RECORDS),
    )


def raw_responses_for_run(
    store: HarnessSessionStore,
    run_id: str,
    *,
    limit: int = MAX_UI_RECORDS,
) -> tuple[HarnessRawRecord, ...]:
    """Return a bounded chronological raw-response window for one run."""
    return store.list_raw_responses_for_run(
        run_id,
        limit=min(max(limit, 1), MAX_UI_RECORDS),
    )
