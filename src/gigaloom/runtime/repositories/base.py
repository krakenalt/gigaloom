"""Shared SQLite lifecycle for runtime repositories."""

from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from typing import Iterator, Self

from gigaloom.runtime.db import DbProvider


class RuntimeRepository:
    """Provide one database boundary to composed coordination repositories."""

    def __init__(self, db: DbProvider) -> None:
        self._db = db

    def close(self) -> None:
        """Close database resources owned by this repository set."""
        self._db.close()

    def __enter__(self) -> Self:
        """Return this repository set as a managed resource."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close database resources when leaving a managed scope."""
        self.close()

    @property
    def schema_version(self) -> int:
        """Return the currently applied schema version."""
        with self._connect() as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._db.connect() as connection:
            yield connection
