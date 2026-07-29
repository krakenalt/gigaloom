"""Content-free counters for SQLite-backed runtime workloads."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3
import threading
from typing import Iterator

from gpt2giga_harness.runtime.store import RuntimeCoordinationStore


@dataclass(frozen=True, slots=True)
class SqlCounts:
    """Observed SQLite activity without retaining statement text."""

    connections: int = 0
    reads: int = 0
    writes: int = 0
    schema: int = 0

    def __sub__(self, other: SqlCounts) -> SqlCounts:
        return SqlCounts(
            connections=max(self.connections - other.connections, 0),
            reads=max(self.reads - other.reads, 0),
            writes=max(self.writes - other.writes, 0),
            schema=max(self.schema - other.schema, 0),
        )


@dataclass
class RuntimeCounters:
    """Stable algorithmic counters attached to one measured operation."""

    sqlite_connections: int = 0
    sqlite_statements: int = 0
    sqlite_reads: int = 0
    sqlite_writes: int = 0
    rows_parsed: int = 0
    claimed_jobs: int = 0
    duplicate_claims: int = 0
    wakeups: int = 0
    maintenance_cycles: int = 0

    def snapshot(self) -> dict[str, int]:
        """Return a detached counter mapping."""
        return asdict(self)


class TracingRuntimeStore(RuntimeCoordinationStore):
    """Count SQLite operations while discarding all statement content."""

    def __init__(self, data_dir: str | Path) -> None:
        self._trace_lock = threading.Lock()
        self._trace_counts = SqlCounts()
        super().__init__(data_dir)

    def trace_snapshot(self) -> SqlCounts:
        """Return the current aggregate trace counters."""
        with self._trace_lock:
            return self._trace_counts

    def _record_statement(self, statement: str) -> None:
        operation = statement.lstrip().split(None, 1)[0].upper() if statement else ""
        with self._trace_lock:
            current = self._trace_counts
            self._trace_counts = SqlCounts(
                connections=current.connections,
                reads=current.reads + int(operation in {"SELECT", "WITH"}),
                writes=current.writes
                + int(operation in {"INSERT", "UPDATE", "DELETE", "REPLACE"}),
                schema=current.schema + int(operation in {"CREATE", "ALTER", "DROP"}),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with super()._connect() as connection:
            with self._trace_lock:
                current = self._trace_counts
                self._trace_counts = SqlCounts(
                    connections=current.connections + 1,
                    reads=current.reads,
                    writes=current.writes,
                    schema=current.schema,
                )
            connection.set_trace_callback(self._record_statement)
            try:
                yield connection
            finally:
                connection.set_trace_callback(None)


def apply_sql_delta(
    counters: RuntimeCounters,
    before: SqlCounts,
    after: SqlCounts,
) -> None:
    """Copy one operation's trace delta into its stable counters."""
    delta = after - before
    counters.sqlite_connections = delta.connections
    counters.sqlite_reads = delta.reads
    counters.sqlite_writes = delta.writes
    counters.sqlite_statements = delta.reads + delta.writes + delta.schema
