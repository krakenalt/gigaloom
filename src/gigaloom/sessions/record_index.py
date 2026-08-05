"""Derived SQLite projections for bounded retained-record queries."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from gigaloom.sessions.api import (
    EDITED_FROM_MESSAGE_ID,
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
    event_from_dict,
    event_to_dict,
    message_from_dict,
    message_to_dict,
    raw_record_from_dict,
    raw_record_to_dict,
    run_from_dict,
)

RECORD_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS read_index_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    active INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS read_index_messages_active_idx
    ON read_index_messages(session_id, active, position);
CREATE TABLE IF NOT EXISTS read_index_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS read_index_events_session_idx
    ON read_index_events(session_id, position);
CREATE TABLE IF NOT EXISTS read_index_raw_records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS read_index_raw_run_idx
    ON read_index_raw_records(kind, run_id, position);
"""


class SessionRecordIndexMixin:
    """SQLite operations added by the bounded record-query contract."""

    def _connect(self) -> Any:
        raise NotImplementedError

    def _meta(self, connection: sqlite3.Connection, key: str) -> str:
        raise NotImplementedError

    def _run_generation(self, connection: sqlite3.Connection) -> int:
        raise NotImplementedError

    def _upsert_run(
        self,
        connection: sqlite3.Connection,
        run: HarnessRun,
        position: int,
    ) -> None:
        raise NotImplementedError

    def _set_meta(
        self,
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        raise NotImplementedError

    def records_complete(self) -> bool:
        """Return whether retained messages, events, and raw rows are indexed."""
        with self._connect() as connection:
            return self._meta(connection, "records_complete") == "1"

    def mark_records_complete(self, complete: bool) -> None:
        """Mark whether the record projection matches authoritative JSONL."""
        with self._connect() as connection:
            self._set_meta(
                connection,
                "records_complete",
                str(int(complete)),
            )

    def append_run(self, run: HarnessRun) -> None:
        """Append one run at the next stable session position."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(position) + 1, 0)
                FROM read_index_runs
                WHERE session_id = ?
                """,
                (run.session_id,),
            ).fetchone()
            self._upsert_run(connection, run, int(row[0]) if row else 0)
            if self._meta(connection, "complete") == "1":
                self._set_meta(
                    connection,
                    "run_generation",
                    str(self._run_generation(connection) + 1),
                )

    def list_runs_page(
        self,
        session_id: str,
        *,
        cursor: RunPageCursor | None,
        limit: int,
    ) -> RunPage:
        """Return one bounded newest-first run page."""
        bounded = bounded_query_limit(limit, maximum=MAX_RECORD_QUERY_LIMIT)
        with self._connect() as connection:
            generation = self._run_generation(connection)
            if cursor is not None:
                if cursor.session_id != session_id:
                    raise ValueError("run cursor belongs to another session")
                if cursor.generation != generation:
                    raise StaleReadSnapshotError("run cursor snapshot is stale")
            clauses = ["session_id = ?"]
            values: list[object] = [session_id]
            if cursor is not None:
                clauses.append("(position < ? OR (position = ? AND id < ?))")
                values.extend((cursor.position, cursor.position, cursor.run_id))
            rows = connection.execute(
                f"""
                SELECT id, position, payload_json
                FROM read_index_runs
                WHERE {" AND ".join(clauses)}
                ORDER BY position DESC, id DESC
                LIMIT ?
                """,
                (*values, bounded + 1),
            ).fetchall()
        has_more = len(rows) > bounded
        page_rows = rows[:bounded]
        items = tuple(run_from_dict(json.loads(str(row[2]))) for row in page_rows)
        next_cursor = (
            RunPageCursor(
                generation=generation,
                session_id=session_id,
                position=int(page_rows[-1][1]),
                run_id=str(page_rows[-1][0]),
            )
            if has_more
            else None
        )
        return RunPage(items, next_cursor, has_more, generation)

    def latest_runs(
        self,
        session_ids: tuple[str, ...],
    ) -> dict[str, HarnessRun | None]:
        """Return one newest run per requested session in a single index query."""
        if len(session_ids) > MAX_RECORD_QUERY_LIMIT:
            raise ValueError(
                f"session_ids must contain at most {MAX_RECORD_QUERY_LIMIT} items"
            )
        result: dict[str, HarnessRun | None] = dict.fromkeys(session_ids)
        if not session_ids:
            return result
        placeholders = ", ".join("?" for _ in session_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                WITH ranked_runs AS (
                    SELECT session_id, payload_json,
                           ROW_NUMBER() OVER (
                               PARTITION BY session_id
                               ORDER BY position DESC, id DESC
                           ) AS rank
                    FROM read_index_runs
                    WHERE session_id IN ({placeholders})
                )
                SELECT session_id, payload_json
                FROM ranked_runs
                WHERE rank = 1
                """,
                session_ids,
            ).fetchall()
        for session_id, payload_json in rows:
            result[str(session_id)] = run_from_dict(json.loads(str(payload_json)))
        return result

    def record_message(self, message: HarnessMessage, position: int) -> None:
        """Retain one message and update the active edit projection."""
        with self._connect() as connection:
            self._record_message(connection, message, position)

    def lookup_message(self, message_id: str) -> HarnessMessage | None:
        """Resolve one retained message without scanning its JSONL file."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM read_index_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return message_from_dict(json.loads(str(row[0]))) if row is not None else None

    def list_recent_messages(
        self,
        session_id: str,
        *,
        limit: int,
        before: str | None,
        through: str | None,
    ) -> tuple[HarnessMessage, ...]:
        """Return a bounded active chronological message window."""
        if before is not None and through is not None:
            raise ValueError("before and through are mutually exclusive")
        bounded = bounded_query_limit(limit, maximum=MAX_MESSAGE_QUERY_LIMIT)
        clauses = ["session_id = ?", "active = 1"]
        values: list[object] = [session_id]
        anchor = before or through
        with self._connect() as connection:
            if anchor is not None:
                row = connection.execute(
                    """
                    SELECT position FROM read_index_messages
                    WHERE id = ? AND session_id = ? AND active = 1
                    """,
                    (anchor, session_id),
                ).fetchone()
                if row is None:
                    raise MessageNotFoundError(anchor)
                clauses.append(f"position {'<' if before else '<='} ?")
                values.append(int(row[0]))
            rows = connection.execute(
                f"""
                SELECT payload_json FROM read_index_messages
                WHERE {" AND ".join(clauses)}
                ORDER BY position DESC
                LIMIT ?
                """,
                (*values, bounded),
            ).fetchall()
        return tuple(
            message_from_dict(json.loads(str(row[0]))) for row in reversed(rows)
        )

    def record_event(self, event: HarnessStoredEvent, position: int) -> None:
        """Retain one event for direct lookup."""
        with self._connect() as connection:
            self._record_event(connection, event, position)

    def lookup_event(self, event_id: str) -> HarnessStoredEvent | None:
        """Resolve one retained event without scanning its JSONL file."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM read_index_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return event_from_dict(json.loads(str(row[0]))) if row is not None else None

    def record_raw(
        self,
        kind: str,
        record: HarnessRawRecord,
        position: int,
    ) -> None:
        """Retain one request or response for bounded run lookup."""
        with self._connect() as connection:
            self._record_raw(connection, kind, record, position)

    def list_raw_for_run(
        self,
        kind: str,
        run_id: str,
        *,
        limit: int,
    ) -> tuple[HarnessRawRecord, ...]:
        """Return a bounded chronological raw-record window."""
        bounded = bounded_query_limit(limit, maximum=MAX_RECORD_QUERY_LIMIT)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM read_index_raw_records
                WHERE kind = ? AND run_id = ?
                ORDER BY position DESC
                LIMIT ?
                """,
                (kind, run_id, bounded),
            ).fetchall()
        return tuple(
            raw_record_from_dict(json.loads(str(row[0]))) for row in reversed(rows)
        )

    @staticmethod
    def _record_message(
        connection: sqlite3.Connection,
        message: HarnessMessage,
        position: int,
    ) -> None:
        edited_from = message.metadata.get(EDITED_FROM_MESSAGE_ID)
        if isinstance(edited_from, str):
            row = connection.execute(
                """
                SELECT position FROM read_index_messages
                WHERE id = ? AND session_id = ? AND active = 1
                """,
                (edited_from, message.session_id),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """
                    UPDATE read_index_messages SET active = 0
                    WHERE session_id = ? AND active = 1 AND position >= ?
                    """,
                    (message.session_id, int(row[0])),
                )
        connection.execute(
            """
            INSERT INTO read_index_messages(
                id, session_id, position, active, payload_json
            ) VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_id = excluded.session_id,
                position = excluded.position,
                active = excluded.active,
                payload_json = excluded.payload_json
            """,
            (
                message.id,
                message.session_id,
                max(position, 0),
                json.dumps(
                    message_to_dict(message), ensure_ascii=False, sort_keys=True
                ),
            ),
        )

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        event: HarnessStoredEvent,
        position: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO read_index_events(
                id, session_id, run_id, position, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_id = excluded.session_id,
                run_id = excluded.run_id,
                position = excluded.position,
                payload_json = excluded.payload_json
            """,
            (
                event.id,
                event.session_id,
                event.run_id,
                max(position, 0),
                json.dumps(event_to_dict(event), ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _record_raw(
        connection: sqlite3.Connection,
        kind: str,
        record: HarnessRawRecord,
        position: int,
    ) -> None:
        if kind not in {"request", "response"}:
            raise ValueError("raw record kind must be request or response")
        connection.execute(
            """
            INSERT INTO read_index_raw_records(
                id, kind, session_id, run_id, position, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind = excluded.kind,
                session_id = excluded.session_id,
                run_id = excluded.run_id,
                position = excluded.position,
                payload_json = excluded.payload_json
            """,
            (
                record.id,
                kind,
                record.session_id,
                record.run_id,
                max(position, 0),
                json.dumps(
                    raw_record_to_dict(record), ensure_ascii=False, sort_keys=True
                ),
            ),
        )
