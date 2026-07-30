"""Store durable native process coordination state."""

from __future__ import annotations

from typing import Any, Mapping

from gpt2giga_harness.runtime.db.transactions import transaction as _transaction
from gpt2giga_harness.runtime.models import (
    NativeProcessOutputRecord,
    NativeProcessRecord,
)
from gpt2giga_harness.runtime.repositories.base import RuntimeRepository
from gpt2giga_harness.runtime.repositories.errors import (
    NativeProcessRecordNotFoundError,
)
from gpt2giga_harness.runtime.repositories.records import (
    _future_time,
    _native_process_from_row,
    _native_process_output_from_row,
    _optional_text,
    _safe_json,
    _utc_now,
)
from gpt2giga_harness.sessions.contracts import redact_for_storage


class NativeProcessRepository(RuntimeRepository):
    """Store durable native process coordination state."""

    def create_native_process(self, record: NativeProcessRecord) -> NativeProcessRecord:
        """Persist one native process before it is exposed to API clients."""
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                INSERT INTO native_processes (
                    id, owner_id, owner_process_id, session_id, run_id,
                    harness_id, status, process_id, process_group_id, transport,
                    ref_json, started_at, updated_at, heartbeat_at, leased_until,
                    timeout_at, cancel_requested_at, finished_at, terminal_cursor,
                    recovery_outcome, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.owner_id,
                    record.owner_process_id,
                    record.session_id,
                    record.run_id,
                    record.harness_id,
                    record.status,
                    record.process_id,
                    record.process_group_id,
                    record.transport,
                    _safe_json(record.ref),
                    record.started_at,
                    record.updated_at,
                    record.heartbeat_at,
                    record.leased_until,
                    record.timeout_at,
                    record.cancel_requested_at,
                    record.finished_at,
                    record.terminal_cursor,
                    record.recovery_outcome,
                    record.version,
                ),
            )
            row = connection.execute(
                "SELECT * FROM native_processes WHERE id = ?", (record.id,)
            ).fetchone()
        return _native_process_from_row(row)

    def get_native_process(self, process_id: str) -> NativeProcessRecord:
        """Return one durable native process record."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM native_processes WHERE id = ?", (process_id,)
            ).fetchone()
        if row is None:
            raise NativeProcessRecordNotFoundError(process_id)
        return _native_process_from_row(row)

    def list_native_processes(self) -> tuple[NativeProcessRecord, ...]:
        """List durable native process records in creation order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM native_processes ORDER BY started_at, id"
            ).fetchall()
        return tuple(_native_process_from_row(row) for row in rows)

    def heartbeat_native_process(
        self,
        process_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
        ref: Mapping[str, Any],
        terminal_cursor: int,
    ) -> NativeProcessRecord:
        """Renew a native owner lease while refreshing its public snapshot."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE native_processes
                SET heartbeat_at = ?, leased_until = ?, updated_at = ?,
                    ref_json = ?, terminal_cursor = MAX(terminal_cursor, ?),
                    version = version + 1
                WHERE id = ? AND owner_id = ? AND status = 'running'
                """,
                (
                    now,
                    _future_time(max(lease_seconds, 0.1)),
                    now,
                    _safe_json(ref),
                    max(terminal_cursor, 0),
                    process_id,
                    owner_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM native_processes WHERE id = ?", (process_id,)
            ).fetchone()
        if row is None:
            raise NativeProcessRecordNotFoundError(process_id)
        return _native_process_from_row(row)

    def append_native_process_output(
        self,
        output: NativeProcessOutputRecord,
        *,
        owner_id: str,
        max_chunks: int,
    ) -> None:
        """Persist one redacted terminal chunk and prune older references."""
        with self._connect() as connection, _transaction(connection):
            owner = connection.execute(
                "SELECT owner_id FROM native_processes WHERE id = ?",
                (output.process_id,),
            ).fetchone()
            if owner is None:
                raise NativeProcessRecordNotFoundError(output.process_id)
            if str(owner["owner_id"]) != owner_id:
                return
            connection.execute(
                """
                INSERT OR IGNORE INTO native_process_outputs (
                    process_id, cursor, stream, text, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    output.process_id,
                    output.cursor,
                    output.stream,
                    str(redact_for_storage(output.text)),
                    output.created_at,
                ),
            )
            connection.execute(
                """
                UPDATE native_processes
                SET terminal_cursor = MAX(terminal_cursor, ?), updated_at = ?,
                    version = version + 1
                WHERE id = ? AND owner_id = ?
                """,
                (output.cursor, _utc_now(), output.process_id, owner_id),
            )
            keep = max(int(max_chunks), 1)
            connection.execute(
                """
                DELETE FROM native_process_outputs
                WHERE process_id = ? AND cursor <= ?
                """,
                (output.process_id, max(output.cursor - keep, 0)),
            )

    def read_native_process_outputs(
        self, process_id: str, *, after_cursor: int = 0
    ) -> tuple[NativeProcessOutputRecord, ...]:
        """Read persisted terminal chunks after one caller cursor."""
        self.get_native_process(process_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM native_process_outputs
                WHERE process_id = ? AND cursor > ? ORDER BY cursor
                """,
                (process_id, max(after_cursor, 0)),
            ).fetchall()
        return tuple(_native_process_output_from_row(row) for row in rows)

    def request_native_process_cancel(self, process_id: str) -> NativeProcessRecord:
        """Persist a cooperative cancellation request for the owning supervisor."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE native_processes
                SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    updated_at = ?, version = version + 1
                WHERE id = ? AND status = 'running'
                """,
                (now, now, process_id),
            )
            row = connection.execute(
                "SELECT * FROM native_processes WHERE id = ?", (process_id,)
            ).fetchone()
        if row is None:
            raise NativeProcessRecordNotFoundError(process_id)
        return _native_process_from_row(row)

    def finish_native_process(
        self,
        process_id: str,
        *,
        owner_id: str,
        status: str,
        ref: Mapping[str, Any],
        terminal_cursor: int,
        recovery_outcome: str | None = None,
    ) -> NativeProcessRecord:
        """Finish a native process only from its proven owner."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE native_processes
                SET status = ?, ref_json = ?, terminal_cursor = MAX(terminal_cursor, ?),
                    recovery_outcome = ?, finished_at = COALESCE(finished_at, ?),
                    heartbeat_at = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND owner_id = ? AND status = 'running'
                """,
                (
                    status,
                    _safe_json(ref),
                    max(terminal_cursor, 0),
                    _optional_text(recovery_outcome),
                    now,
                    now,
                    now,
                    process_id,
                    owner_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM native_processes WHERE id = ?", (process_id,)
            ).fetchone()
        if row is None:
            raise NativeProcessRecordNotFoundError(process_id)
        return _native_process_from_row(row)

    def list_expired_native_processes(self) -> tuple[NativeProcessRecord, ...]:
        """List running native processes whose owner lease expired."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM native_processes
                WHERE status = 'running' AND leased_until < ?
                ORDER BY leased_until, id
                """,
                (_utc_now(),),
            ).fetchall()
        return tuple(_native_process_from_row(row) for row in rows)

    def recover_native_process(
        self,
        process_id: str,
        *,
        status: str,
        ref: Mapping[str, Any],
        recovery_outcome: str,
    ) -> NativeProcessRecord:
        """Record an expired owner outcome without attempting process adoption."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE native_processes
                SET status = ?, ref_json = ?, recovery_outcome = ?,
                    finished_at = COALESCE(finished_at, ?), updated_at = ?,
                    version = version + 1
                WHERE id = ? AND status = 'running' AND leased_until < ?
                """,
                (status, _safe_json(ref), recovery_outcome, now, now, process_id, now),
            )
            row = connection.execute(
                "SELECT * FROM native_processes WHERE id = ?", (process_id,)
            ).fetchone()
        if row is None:
            raise NativeProcessRecordNotFoundError(process_id)
        return _native_process_from_row(row)
