"""Run-record repository contracts and the legacy JSONL implementation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from gpt2giga_harness.sessions.locking import exclusive_file_lock
from gpt2giga_harness.sessions.models import (
    HarnessRun,
    run_from_dict,
    run_to_dict,
)
from gpt2giga_harness.sessions.read_index import SessionReadIndex
from gpt2giga_harness.sessions.redaction import redact_for_storage
from gpt2giga_harness.sessions.store import (
    RunNotFoundError,
    _patch_run,
    new_id,
)

RUNS_FILE = "runs.jsonl"


class RunRepository(Protocol):
    """Persistence boundary for session run records."""

    def append(self, run: HarnessRun) -> HarnessRun:
        """Append one run record."""

    def update(self, run_id: str, **patch: Any) -> HarnessRun:
        """Patch one run record."""

    def get(self, run_id: str) -> HarnessRun:
        """Return one run by id."""

    def list(self, session_id: str) -> tuple[HarnessRun, ...]:
        """Return runs for one session in append order."""


class LegacyJsonlRunRepository:
    """Preserve the authoritative ``runs.jsonl`` persistence behavior."""

    def __init__(
        self,
        *,
        session_dir: Callable[[str], Path],
        current_read_index: Callable[[], SessionReadIndex | None],
        read_index: Callable[[], SessionReadIndex],
        ensure_read_index: Callable[[], None],
        rebuild_read_index: Callable[[], None],
        publish_runs_center: Callable[[], None],
    ) -> None:
        self._session_dir = session_dir
        self._current_read_index = current_read_index
        self._read_index = read_index
        self._ensure_read_index = ensure_read_index
        self._rebuild_read_index = rebuild_read_index
        self._publish_runs_center = publish_runs_center

    def append(self, run: HarnessRun) -> HarnessRun:
        """Append one run to the legacy authoritative log."""
        _append_jsonl(self._path(run.session_id), run_to_dict(run))
        read_index = self._current_read_index()
        if read_index is not None:
            read_index.append_run(run)
        self._publish_runs_center()
        return run

    def update(self, run_id: str, **patch: Any) -> HarnessRun:
        """Patch one run by rewriting its legacy authoritative log."""
        self._ensure_read_index()
        indexed = self._read_index().lookup_run(run_id)
        if indexed is None:
            raise RunNotFoundError(run_id)
        for attempt in range(2):
            session_id = indexed[0]
            path = self._path(session_id)
            with exclusive_file_lock(path):
                runs = _read_jsonl(path)
                for index, run in enumerate(runs):
                    if run.id != run_id:
                        continue
                    updated = _patch_run(run, patch)
                    runs[index] = updated
                    _write_jsonl_atomic_unlocked(
                        path,
                        [redact_for_storage(run_to_dict(item)) for item in runs],
                    )
                    self._read_index().upsert_run(updated, index)
                    self._publish_runs_center()
                    return updated
            if attempt == 0:
                # The JSONL log is authoritative. Rebuild the derived index once
                # if another writer left its row pointing at the wrong session.
                self._rebuild_read_index()
                indexed = self._read_index().lookup_run(run_id)
                if indexed is None:
                    break
        raise RunNotFoundError(run_id)

    def get(self, run_id: str) -> HarnessRun:
        """Resolve one run through the rebuildable read index."""
        self._ensure_read_index()
        indexed = self._read_index().lookup_run(run_id)
        if indexed is None:
            raise RunNotFoundError(run_id)
        return indexed[2]

    def list(self, session_id: str) -> tuple[HarnessRun, ...]:
        """Read the legacy authoritative log in append order."""
        return tuple(_read_jsonl(self._path(session_id)))

    def _path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / RUNS_FILE


def _read_jsonl(path: Path) -> list[HarnessRun]:
    if not path.exists():
        return []
    rows: list[HarnessRun] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            decoded = json.loads(text)
            if isinstance(decoded, Mapping):
                rows.append(run_from_dict(decoded))
    return rows


def _write_jsonl_atomic_unlocked(path: Path, payloads: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{new_id('tmp')}")
    with temp_path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(redact_for_storage(payload), ensure_ascii=False))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _append_jsonl(path: Path, payload: Any) -> None:
    with exclusive_file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(redact_for_storage(payload), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.lseek(descriptor, 0, os.SEEK_END)
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
