"""Scoped algorithmic counters for filesystem session workloads."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator
import threading

import gigaloom.sessions.filesystem as filesystem
import gigaloom.sessions.read_index as read_index
import gigaloom.sessions.storage.filesystem.runs as run_storage

_INSTRUMENTATION_LOCK = threading.RLock()


@dataclass
class StorageCounters:
    """Observable I/O and parsing work performed inside one measured window."""

    bytes_read: int = 0
    bytes_written: int = 0
    files_opened: int = 0
    manifest_reads: int = 0
    index_reads: int = 0
    atomic_replaces: int = 0
    fsync_calls: int = 0
    rows_parsed: int = 0
    sqlite_connections: int = 0
    sqlite_statements: int = 0

    def record_direct_read(self, *, byte_count: int, rows: int) -> None:
        """Record one bounded reader implemented outside JSON helpers."""
        self.files_opened += 1
        self.bytes_read += max(byte_count, 0)
        self.rows_parsed += max(rows, 0)

    def snapshot(self) -> dict[str, int]:
        """Return a detached, stable counter mapping."""
        return asdict(self)


class _SQLiteProxy:
    def __init__(self, module: ModuleType, counters: StorageCounters) -> None:
        self._module = module
        self._counters = counters

    def connect(self, *args: Any, **kwargs: Any) -> Any:
        connection = self._module.connect(*args, **kwargs)
        self._counters.sqlite_connections += 1
        connection.set_trace_callback(self._record_statement)
        return connection

    def _record_statement(self, _statement: str) -> None:
        self._counters.sqlite_statements += 1

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)


@contextmanager
def observe_session_storage() -> Iterator[StorageCounters]:
    """Count session persistence work without changing production algorithms."""
    _INSTRUMENTATION_LOCK.acquire()
    counters = StorageCounters()
    original_read_json = filesystem._read_json
    original_read_jsonl = filesystem._read_jsonl
    original_write_json = filesystem._write_json_atomic_unlocked
    original_write_jsonl = filesystem._write_jsonl_atomic_unlocked
    original_append_jsonl = filesystem._append_jsonl
    original_run_read_json = run_storage._read_json
    original_run_write_authoritative_json = run_storage._write_authoritative_json
    original_run_write_derived_json = run_storage._write_derived_json
    original_sqlite = read_index.sqlite3

    def read_json(path: Path) -> dict[str, Any]:
        counters.files_opened += 1
        counters.bytes_read += _path_size(path)
        if path.name == filesystem.MANIFEST_FILE:
            counters.manifest_reads += 1
        elif path.name == filesystem.INDEX_FILE:
            counters.index_reads += 1
        return original_read_json(path)

    def read_jsonl(path: Path, parser: Any) -> list[Any]:
        if not path.exists():
            return original_read_jsonl(path, parser)
        counters.files_opened += 1
        counters.bytes_read += _path_size(path)
        rows = original_read_jsonl(path, parser)
        counters.rows_parsed += len(rows)
        return rows

    def write_json(path: Path, payload: Any) -> None:
        original_write_json(path, payload)
        _record_atomic_write(counters, path)

    def write_jsonl(path: Path, payloads: list[Any]) -> None:
        original_write_jsonl(path, payloads)
        _record_atomic_write(counters, path)

    def append_jsonl(path: Path, payload: Any) -> None:
        before = _path_size(path)
        original_append_jsonl(path, payload)
        counters.files_opened += 1
        counters.bytes_written += max(_path_size(path) - before, 0)
        counters.fsync_calls += 1

    def read_run_json(path: Path) -> dict[str, Any]:
        counters.files_opened += 1
        counters.bytes_read += _path_size(path)
        payload = original_run_read_json(path)
        if path.parent.name == run_storage.RUN_RECORDS_DIR:
            counters.rows_parsed += 1
        return payload

    def write_authoritative_run_json(path: Path, payload: Any) -> None:
        original_run_write_authoritative_json(path, payload)
        _record_atomic_write(counters, path)

    def write_derived_run_json(path: Path, payload: Any) -> None:
        original_run_write_derived_json(path, payload)
        _record_atomic_write(counters, path, fsync=False)

    filesystem._read_json = read_json
    filesystem._read_jsonl = read_jsonl
    filesystem._write_json_atomic_unlocked = write_json
    filesystem._write_jsonl_atomic_unlocked = write_jsonl
    filesystem._append_jsonl = append_jsonl
    run_storage._read_json = read_run_json
    run_storage._write_authoritative_json = write_authoritative_run_json
    run_storage._write_derived_json = write_derived_run_json
    read_index.sqlite3 = _SQLiteProxy(original_sqlite, counters)
    try:
        yield counters
    finally:
        filesystem._read_json = original_read_json
        filesystem._read_jsonl = original_read_jsonl
        filesystem._write_json_atomic_unlocked = original_write_json
        filesystem._write_jsonl_atomic_unlocked = original_write_jsonl
        filesystem._append_jsonl = original_append_jsonl
        run_storage._read_json = original_run_read_json
        run_storage._write_authoritative_json = original_run_write_authoritative_json
        run_storage._write_derived_json = original_run_write_derived_json
        read_index.sqlite3 = original_sqlite
        _INSTRUMENTATION_LOCK.release()


def _record_atomic_write(
    counters: StorageCounters,
    path: Path,
    *,
    fsync: bool = True,
) -> None:
    counters.files_opened += 1
    counters.bytes_written += _path_size(path)
    counters.atomic_replaces += 1
    counters.fsync_calls += int(fsync)


def _path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0
