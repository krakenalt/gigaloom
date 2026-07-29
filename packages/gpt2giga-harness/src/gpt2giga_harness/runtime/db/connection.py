"""SQLite connection factory and lifecycle boundary."""

from __future__ import annotations

from contextlib import contextmanager
from os import getpid
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Iterator

SQLITE_TIMEOUT_SECONDS = 10.0


class DbProvider:
    """Create operation-scoped SQLite connections for runtime repositories.

    Thread-local or bounded reuse would keep the exposed raw connection alive
    after its context exits, so connections remain short-lived to preserve that
    lifecycle contract and avoid ambiguous thread ownership. Database-wide WAL
    setup is cached per process, while connection-local safety settings are
    applied to every connection.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = SQLITE_TIMEOUT_SECONDS,
    ) -> None:
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self._closed = False
        self._process_id = getpid()
        self._journal_mode_pid: int | None = None
        self._state_lock = Lock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield one configured connection and close it after use."""
        process_id = self._refresh_process_state()
        with self._state_lock:
            if self._closed:
                raise RuntimeError("database provider is closed")
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self._ensure_journal_mode(connection, process_id)
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    def close(self) -> None:
        """Close provider-owned resources and reject new connections."""
        self._refresh_process_state()
        with self._state_lock:
            self._closed = True

    def _refresh_process_state(self) -> int:
        """Reset process-local synchronization and setup after a fork."""
        process_id = getpid()
        if process_id != self._process_id:
            self._process_id = process_id
            self._journal_mode_pid = None
            self._state_lock = Lock()
        return process_id

    def _ensure_journal_mode(
        self,
        connection: sqlite3.Connection,
        process_id: int,
    ) -> None:
        """Set persistent journal mode once for this provider and process."""
        with self._state_lock:
            if self._closed:
                raise RuntimeError("database provider is closed")
            if self._journal_mode_pid == process_id:
                return
            connection.execute("PRAGMA journal_mode = WAL")
            self._journal_mode_pid = process_id
