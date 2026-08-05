"""In-memory bounded query implementation and shared query validation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from gigaloom.sessions.api import (
    EDITED_FROM_MESSAGE_ID,
    EventNotFoundError,
    MAX_MESSAGE_QUERY_LIMIT,
    MAX_RECORD_QUERY_LIMIT,
    MessageNotFoundError,
    RunPage,
    RunPageCursor,
    StaleReadSnapshotError,
    bounded_query_limit,
)
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessRawRecord,
    HarnessRun,
    HarnessStoredEvent,
)


class InMemoryQueryIndex:
    """Direct retained-record lookups plus the active message projection."""

    def __init__(self) -> None:
        self.messages: dict[str, HarnessMessage] = {}
        self.active_messages: dict[str, list[str]] = defaultdict(list)
        self.events: dict[str, HarnessStoredEvent] = {}
        self.raw_requests: dict[str, list[HarnessRawRecord]] = defaultdict(list)
        self.raw_responses: dict[str, list[HarnessRawRecord]] = defaultdict(list)

    def record_message(self, message: HarnessMessage) -> None:
        """Retain one message and advance its active edit branch."""
        self.messages[message.id] = message
        active = self.active_messages[message.session_id]
        edited_from = message.metadata.get(EDITED_FROM_MESSAGE_ID)
        if isinstance(edited_from, str) and edited_from in active:
            del active[active.index(edited_from) :]
        active.append(message.id)

    def recent_messages(
        self,
        session_id: str,
        *,
        limit: int,
        before: str | None,
        through: str | None,
    ) -> tuple[HarnessMessage, ...]:
        """Return a bounded active branch without retained superseded rows."""
        _validate_message_bounds(before, through)
        active = self.active_messages.get(session_id, [])
        end = len(active)
        anchor = before or through
        if anchor is not None:
            try:
                end = active.index(anchor) + int(through is not None)
            except ValueError as exc:
                raise MessageNotFoundError(anchor) from exc
        start = max(
            0, end - bounded_query_limit(limit, maximum=MAX_MESSAGE_QUERY_LIMIT)
        )
        return tuple(self.messages[item] for item in active[start:end])

    def record_event(self, event: HarnessStoredEvent) -> None:
        """Retain one direct event lookup."""
        self.events[event.id] = event

    def record_raw(self, kind: str, record: HarnessRawRecord) -> None:
        """Retain one run-scoped raw record."""
        target = self.raw_requests if kind == "request" else self.raw_responses
        target[record.run_id].append(record)

    def raw_for_run(
        self,
        kind: str,
        run_id: str,
        *,
        limit: int,
    ) -> tuple[HarnessRawRecord, ...]:
        """Return the most recent bounded raw window in chronological order."""
        bounded = bounded_query_limit(limit, maximum=MAX_RECORD_QUERY_LIMIT)
        source = self.raw_requests if kind == "request" else self.raw_responses
        return tuple(source.get(run_id, ())[-bounded:])

    def delete_session(self, session_id: str) -> None:
        """Forget all direct rows owned by one deleted session."""
        active = self.active_messages.pop(session_id, ())
        for message_id in active:
            self.messages.pop(message_id, None)
        self.messages = {
            key: value
            for key, value in self.messages.items()
            if value.session_id != session_id
        }
        self.events = {
            key: value
            for key, value in self.events.items()
            if value.session_id != session_id
        }
        for target in (self.raw_requests, self.raw_responses):
            for run_id in tuple(target):
                target[run_id] = [
                    record
                    for record in target[run_id]
                    if record.session_id != session_id
                ]
                if not target[run_id]:
                    target.pop(run_id, None)


class InMemorySessionQueryMixin:
    """Bounded query methods shared by the in-memory session store."""

    _query_index: InMemoryQueryIndex
    _messages: dict[str, list[HarnessMessage]]
    _runs: dict[str, list[HarnessRun]]
    _run_generation: int

    def get_session(self, session_id: str) -> Any:
        raise NotImplementedError

    def get_session_bundle(self, session_id: str) -> Any:
        raise NotImplementedError

    def list_event_tail_page(self, session_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def list_recent_messages(
        self,
        session_id: str,
        *,
        limit: int,
        before: str | None = None,
        through: str | None = None,
    ) -> tuple[HarnessMessage, ...]:
        self.get_session(session_id)
        return self._query_index.recent_messages(
            session_id,
            limit=limit,
            before=before,
            through=through,
        )

    def get_message(self, message_id: str) -> HarnessMessage:
        try:
            return self._query_index.messages[message_id]
        except KeyError as exc:
            raise MessageNotFoundError(message_id) from exc

    def list_runs_page(
        self,
        session_id: str,
        *,
        cursor: RunPageCursor | None = None,
        limit: int = 50,
    ) -> RunPage:
        self.get_session(session_id)
        bounded = bounded_query_limit(limit, maximum=MAX_RECORD_QUERY_LIMIT)
        if cursor is not None:
            if cursor.session_id != session_id:
                raise ValueError("run cursor belongs to another session")
            if cursor.generation != self._run_generation:
                raise StaleReadSnapshotError("run cursor snapshot is stale")
        runs = self._runs.get(session_id, ())
        end = len(runs) if cursor is None else cursor.position
        positions = list(range(end - 1, max(-1, end - bounded - 1), -1))
        has_more = bool(positions and positions[-1] > 0)
        items = tuple(runs[position] for position in positions)
        next_cursor = (
            RunPageCursor(
                self._run_generation,
                session_id,
                positions[-1],
                items[-1].id,
            )
            if has_more
            else None
        )
        return RunPage(items, next_cursor, has_more, self._run_generation)

    def latest_runs(
        self,
        session_ids: tuple[str, ...],
    ) -> dict[str, HarnessRun | None]:
        """Return newest runs with direct dictionary lookups."""
        if len(session_ids) > MAX_RECORD_QUERY_LIMIT:
            raise ValueError(
                f"session_ids must contain at most {MAX_RECORD_QUERY_LIMIT} items"
            )
        return {
            session_id: runs[-1] if (runs := self._runs.get(session_id)) else None
            for session_id in session_ids
        }

    def list_events_page(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
        max_bytes: int = 1024 * 1024,
    ) -> Any:
        bounded_query_limit(limit, maximum=MAX_RECORD_QUERY_LIMIT)
        return self.list_event_tail_page(
            session_id,
            run_id=run_id,
            offset=offset,
            limit=limit,
            max_bytes=max_bytes,
        )

    def get_event(self, event_id: str) -> HarnessStoredEvent:
        try:
            return self._query_index.events[event_id]
        except KeyError as exc:
            raise EventNotFoundError(event_id) from exc

    def list_raw_requests_for_run(
        self, run_id: str, *, limit: int = 20
    ) -> tuple[HarnessRawRecord, ...]:
        return self._query_index.raw_for_run("request", run_id, limit=limit)

    def list_raw_responses_for_run(
        self, run_id: str, *, limit: int = 20
    ) -> tuple[HarnessRawRecord, ...]:
        return self._query_index.raw_for_run("response", run_id, limit=limit)

    def export_session_bundle(self, session_id: str) -> Any:
        return self.get_session_bundle(session_id)


class FilesystemSessionQueryMixin:
    """Bounded query methods backed by the derived filesystem read model."""

    def get_session(self, session_id: str) -> Any:
        raise NotImplementedError

    def get_session_bundle(self, session_id: str) -> Any:
        raise NotImplementedError

    def list_event_tail_page(self, session_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _ensure_read_index(self) -> None:
        raise NotImplementedError

    def _ensure_record_index(self) -> None:
        raise NotImplementedError

    def _session_read_index(self) -> Any:
        raise NotImplementedError

    def list_recent_messages(
        self,
        session_id: str,
        *,
        limit: int,
        before: str | None = None,
        through: str | None = None,
    ) -> tuple[HarnessMessage, ...]:
        self.get_session(session_id)
        self._ensure_record_index()
        return self._session_read_index().list_recent_messages(
            session_id,
            limit=limit,
            before=before,
            through=through,
        )

    def get_message(self, message_id: str) -> HarnessMessage:
        self._ensure_record_index()
        message = self._session_read_index().lookup_message(message_id)
        if message is None:
            raise MessageNotFoundError(message_id)
        return message

    def list_runs_page(
        self,
        session_id: str,
        *,
        cursor: RunPageCursor | None = None,
        limit: int = 50,
    ) -> RunPage:
        self.get_session(session_id)
        self._ensure_read_index()
        return self._session_read_index().list_runs_page(
            session_id,
            cursor=cursor,
            limit=limit,
        )

    def latest_runs(
        self,
        session_ids: tuple[str, ...],
    ) -> dict[str, HarnessRun | None]:
        """Return newest runs through one derived-index read."""
        self._ensure_read_index()
        return self._session_read_index().latest_runs(session_ids)

    def list_events_page(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
        max_bytes: int = 1024 * 1024,
    ) -> Any:
        bounded_query_limit(limit, maximum=MAX_RECORD_QUERY_LIMIT)
        return self.list_event_tail_page(
            session_id,
            run_id=run_id,
            offset=offset,
            limit=limit,
            max_bytes=max_bytes,
        )

    def get_event(self, event_id: str) -> HarnessStoredEvent:
        self._ensure_record_index()
        event = self._session_read_index().lookup_event(event_id)
        if event is None:
            raise EventNotFoundError(event_id)
        return event

    def list_raw_requests_for_run(
        self, run_id: str, *, limit: int = 20
    ) -> tuple[HarnessRawRecord, ...]:
        self._ensure_record_index()
        return self._session_read_index().list_raw_for_run(
            "request", run_id, limit=limit
        )

    def list_raw_responses_for_run(
        self, run_id: str, *, limit: int = 20
    ) -> tuple[HarnessRawRecord, ...]:
        self._ensure_record_index()
        return self._session_read_index().list_raw_for_run(
            "response", run_id, limit=limit
        )

    def export_session_bundle(self, session_id: str) -> Any:
        return self.get_session_bundle(session_id)


def _validate_message_bounds(
    before: str | None,
    through: str | None,
) -> None:
    if before is not None and through is not None:
        raise ValueError("before and through are mutually exclusive")


def filter_events(
    events: list[HarnessStoredEvent] | tuple[HarnessStoredEvent, ...],
    *,
    run_id: str | None,
    after_id: str | None,
) -> list[HarnessStoredEvent]:
    """Apply the legacy full-export event filters."""
    result = list(events)
    if run_id is not None:
        result = [event for event in result if event.run_id == run_id]
    if after_id is not None:
        seen = False
        filtered: list[HarnessStoredEvent] = []
        for event in result:
            if seen:
                filtered.append(event)
            elif event.id == after_id:
                seen = True
        result = filtered
    return result
