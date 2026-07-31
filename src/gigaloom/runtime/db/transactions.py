"""Transaction helpers with runtime database instrumentation."""

from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from time import perf_counter
from typing import Iterator

from gigaloom.instrumentation import record_duration


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Run an immediate transaction and record lock-wait duration."""
    started_at = perf_counter()
    connection.execute("BEGIN IMMEDIATE")
    record_duration("db_wait_ms", (perf_counter() - started_at) * 1000)
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
