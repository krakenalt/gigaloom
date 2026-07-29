"""SQLite connection factory and lifecycle boundary."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

SQLITE_TIMEOUT_SECONDS = 10.0


class DbProvider:
    """Create configured SQLite connections for runtime repositories."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = SQLITE_TIMEOUT_SECONDS,
    ) -> None:
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self._closed = False

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield one configured connection and close it after use."""
        if self._closed:
            raise RuntimeError("database provider is closed")
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        try:
            yield connection
        finally:
            connection.close()

    def close(self) -> None:
        """Close provider-owned resources and reject new connections."""
        self._closed = True
