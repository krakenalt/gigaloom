"""Recoverable per-run current-state storage and append-order projections."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from gpt2giga_harness.sessions.api import StaleReadSnapshotError
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
RUN_RECORDS_DIR = "run_records"
RUN_ORDER_FILE = "run_order.jsonl"
RUN_REVISION_FILE = "run_revision.json"
RUN_MIGRATION_MARKER = ".run_migration.json"
_RUN_LOCK_TARGET = "run_records"
_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RunStateRecord:
    """One authoritative current run state and its immutable append position."""

    position: int
    run: HarnessRun


@dataclass(frozen=True, slots=True)
class RunPageItem:
    """One run plus byte offsets in the derived append-order projection."""

    offset: int
    next_offset: int
    run: HarnessRun


@dataclass(frozen=True, slots=True)
class RunRepositoryPage:
    """One bounded append-order page of current run records."""

    items: tuple[RunPageItem, ...]
    next_offset: int | None
    has_more: bool
    snapshot_revision: str


@dataclass(frozen=True, slots=True)
class _RunRevision:
    generation: int
    entry_count: int
    state_watermark: int
    order_size: int

    @property
    def snapshot(self) -> str:
        payload = (
            f"{_SCHEMA_VERSION}:{self.generation}:{self.entry_count}:"
            f"{self.state_watermark}:{self.order_size}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()


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

    def list_page(
        self,
        session_id: str,
        *,
        offset: int,
        snapshot_revision: str | None,
        limit: int,
    ) -> RunRepositoryPage:
        """Return one bounded append-order page."""


class FilesystemRunRepository:
    """Store each current run independently behind one per-session lock."""

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
        """Append one authoritative run state and refresh derived order."""
        session_dir = self._session_dir(run.session_id)
        with self._lock(session_dir):
            revision = self._ensure_current_unlocked(session_dir)
            path = _run_state_path(session_dir, run.id)
            if path.exists():
                raise ValueError(f"duplicate run id: {run.id}")
            state = RunStateRecord(revision.entry_count, run)
            _write_run_state(path, state)
            _append_order_entry(session_dir / RUN_ORDER_FILE, state)
            self._publish_revision_unlocked(
                session_dir,
                generation=revision.generation + 1,
                entry_count=revision.entry_count + 1,
            )
            read_index = self._current_read_index()
            if read_index is not None:
                read_index.upsert_run(run, state.position)
        self._publish_runs_center()
        return run

    def update(self, run_id: str, **patch: Any) -> HarnessRun:
        """Patch one current-state file independent of retained history size."""
        self._ensure_read_index()
        indexed = self._read_index().lookup_run(run_id)
        if indexed is None:
            raise RunNotFoundError(run_id)
        for attempt in range(2):
            session_id = indexed[0]
            session_dir = self._session_dir(session_id)
            try:
                with self._lock(session_dir):
                    revision = self._ensure_current_unlocked(session_dir)
                    state = _read_run_state(
                        _run_state_path(session_dir, run_id),
                        expected_id=run_id,
                    )
                    updated = _patch_run(state.run, patch)
                    _write_run_state(
                        _run_state_path(session_dir, run_id),
                        RunStateRecord(state.position, updated),
                    )
                    self._publish_revision_unlocked(
                        session_dir,
                        generation=revision.generation + 1,
                        entry_count=revision.entry_count,
                    )
                    self._read_index().upsert_run(updated, state.position)
                self._publish_runs_center()
                return updated
            except (FileNotFoundError, ValueError):
                if attempt != 0:
                    break
                self._rebuild_read_index()
                indexed = self._read_index().lookup_run(run_id)
                if indexed is None:
                    break
        raise RunNotFoundError(run_id)

    def get(self, run_id: str) -> HarnessRun:
        """Resolve one current state without trusting a cached payload."""
        self._ensure_read_index()
        indexed = self._read_index().lookup_run(run_id)
        if indexed is None:
            raise RunNotFoundError(run_id)
        for attempt in range(2):
            session_dir = self._session_dir(indexed[0])
            try:
                with self._lock(session_dir):
                    self._ensure_current_unlocked(session_dir)
                    return _read_run_state(
                        _run_state_path(session_dir, run_id),
                        expected_id=run_id,
                    ).run
            except (FileNotFoundError, ValueError):
                if attempt != 0:
                    break
                self._rebuild_read_index()
                indexed = self._read_index().lookup_run(run_id)
                if indexed is None:
                    break
        raise RunNotFoundError(run_id)

    def list(self, session_id: str) -> tuple[HarnessRun, ...]:
        """Return the explicit full run export in stable append order."""
        session_dir = self._session_dir(session_id)
        with self._lock(session_dir):
            self._ensure_current_unlocked(session_dir)
            return tuple(state.run for state in _ordered_states(session_dir))

    def list_page(
        self,
        session_id: str,
        *,
        offset: int,
        snapshot_revision: str | None,
        limit: int,
    ) -> RunRepositoryPage:
        """Read only the requested append-order window."""
        session_dir = self._session_dir(session_id)
        with self._lock(session_dir):
            revision = self._ensure_current_unlocked(session_dir)
            if snapshot_revision is not None and snapshot_revision != revision.snapshot:
                raise StaleReadSnapshotError("runs cursor snapshot is stale")
            try:
                items, next_offset, has_more = _read_order_page(
                    session_dir,
                    offset=offset,
                    limit=limit,
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                revision = self._rebuild_projection_unlocked(
                    session_dir,
                    generation=revision.generation + 1,
                )
                if snapshot_revision is not None:
                    raise StaleReadSnapshotError(
                        "runs cursor snapshot is stale"
                    ) from None
                items, next_offset, has_more = _read_order_page(
                    session_dir,
                    offset=offset,
                    limit=limit,
                )
            return RunRepositoryPage(
                items=items,
                next_offset=next_offset,
                has_more=has_more,
                snapshot_revision=revision.snapshot,
            )

    def _ensure_current_unlocked(self, session_dir: Path) -> _RunRevision:
        marker = session_dir / RUN_MIGRATION_MARKER
        if marker.exists():
            return self._migrate_legacy_unlocked(session_dir)
        revision = _read_revision(session_dir / RUN_REVISION_FILE)
        if revision is None:
            state_dir = session_dir / RUN_RECORDS_DIR
            if state_dir.exists() and any(state_dir.glob("*.json")):
                return self._rebuild_projection_unlocked(session_dir, generation=1)
            return self._migrate_legacy_unlocked(session_dir)
        if not _revision_matches(session_dir, revision):
            return self._rebuild_projection_unlocked(
                session_dir,
                generation=revision.generation + 1,
            )
        return revision

    def _migrate_legacy_unlocked(self, session_dir: Path) -> _RunRevision:
        marker = session_dir / RUN_MIGRATION_MARKER
        _write_authoritative_json(
            marker,
            {"schema_version": _SCHEMA_VERSION, "state": "materializing"},
        )
        legacy_runs = _read_legacy_jsonl(session_dir / RUNS_FILE)
        state_dir = session_dir / RUN_RECORDS_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        for path in state_dir.glob("*.json"):
            path.unlink()
        states = tuple(
            RunStateRecord(position, run) for position, run in enumerate(legacy_runs)
        )
        for state in states:
            _write_run_state(
                _run_state_path(session_dir, state.run.id),
                state,
            )
        _write_order_projection(session_dir / RUN_ORDER_FILE, states)
        revision = self._publish_revision_unlocked(
            session_dir,
            generation=1,
            entry_count=len(states),
        )
        marker.unlink(missing_ok=True)
        return revision

    def _rebuild_projection_unlocked(
        self,
        session_dir: Path,
        *,
        generation: int,
    ) -> _RunRevision:
        states = _ordered_states(session_dir)
        _write_order_projection(session_dir / RUN_ORDER_FILE, states)
        return self._publish_revision_unlocked(
            session_dir,
            generation=generation,
            entry_count=len(states),
        )

    @staticmethod
    def _publish_revision_unlocked(
        session_dir: Path,
        *,
        generation: int,
        entry_count: int,
    ) -> _RunRevision:
        revision = _RunRevision(
            generation=max(generation, 1),
            entry_count=max(entry_count, 0),
            state_watermark=_state_watermark(session_dir),
            order_size=_file_size(session_dir / RUN_ORDER_FILE),
        )
        _write_derived_json(
            session_dir / RUN_REVISION_FILE,
            {
                "schema_version": _SCHEMA_VERSION,
                "generation": revision.generation,
                "entry_count": revision.entry_count,
                "state_watermark": revision.state_watermark,
                "order_size": revision.order_size,
            },
        )
        return revision

    @staticmethod
    def _lock(session_dir: Path):
        return exclusive_file_lock(session_dir / _RUN_LOCK_TARGET)


def _read_order_page(
    session_dir: Path,
    *,
    offset: int,
    limit: int,
) -> tuple[tuple[RunPageItem, ...], int | None, bool]:
    path = session_dir / RUN_ORDER_FILE
    file_size = _file_size(path)
    if offset < 0 or offset > file_size:
        raise ValueError("run cursor offset is outside its snapshot")
    items: list[RunPageItem] = []
    next_offset: int | None = None
    has_more = False
    with path.open("rb") as handle:
        handle.seek(offset)
        while len(items) <= limit:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                break
            payload = json.loads(line)
            run_id = str(payload["id"])
            if len(items) == limit:
                has_more = True
                next_offset = line_start
                break
            state = _read_run_state(
                _run_state_path(session_dir, run_id),
                expected_id=run_id,
            )
            items.append(RunPageItem(line_start, handle.tell(), state.run))
    return tuple(items), next_offset, has_more


def _ordered_states(session_dir: Path) -> tuple[RunStateRecord, ...]:
    state_dir = session_dir / RUN_RECORDS_DIR
    if not state_dir.exists():
        return ()
    states = tuple(_read_run_state(path) for path in state_dir.glob("*.json"))
    ordered = tuple(sorted(states, key=lambda state: state.position))
    if tuple(state.position for state in ordered) != tuple(range(len(ordered))):
        raise ValueError("run positions are not contiguous")
    if len({state.run.id for state in ordered}) != len(ordered):
        raise ValueError("run storage contains duplicate ids")
    return ordered


def _read_run_state(
    path: Path,
    *,
    expected_id: str | None = None,
) -> RunStateRecord:
    payload = _read_json(path)
    if int(payload.get("schema_version", 0)) != _SCHEMA_VERSION:
        raise ValueError("unsupported run state schema")
    run_payload = payload.get("run")
    if not isinstance(run_payload, Mapping):
        raise ValueError("run state payload is missing")
    run = run_from_dict(run_payload)
    if expected_id is not None and run.id != expected_id:
        raise ValueError("run state identity mismatch")
    return RunStateRecord(max(int(payload["position"]), 0), run)


def _write_run_state(path: Path, state: RunStateRecord) -> None:
    _write_authoritative_json(
        path,
        {
            "schema_version": _SCHEMA_VERSION,
            "position": state.position,
            "run": redact_for_storage(run_to_dict(state.run)),
        },
    )


def _run_state_path(session_dir: Path, run_id: str) -> Path:
    digest = hashlib.sha256(run_id.encode()).hexdigest()
    return session_dir / RUN_RECORDS_DIR / f"{digest}.json"


def _read_legacy_jsonl(path: Path) -> list[HarnessRun]:
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


def _write_order_projection(
    path: Path,
    states: tuple[RunStateRecord, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{new_id('tmp')}")
    with temp_path.open("w", encoding="utf-8") as handle:
        for state in states:
            handle.write(_order_line(state))
    os.replace(temp_path, path)


def _append_order_entry(path: Path, state: RunStateRecord) -> None:
    encoded = _order_line(state).encode()
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


def _order_line(state: RunStateRecord) -> str:
    return (
        json.dumps(
            {"id": state.run.id, "position": state.position},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def _read_revision(path: Path) -> _RunRevision | None:
    try:
        payload = _read_json(path)
        if int(payload.get("schema_version", 0)) != _SCHEMA_VERSION:
            return None
        return _RunRevision(
            generation=max(int(payload["generation"]), 1),
            entry_count=max(int(payload["entry_count"]), 0),
            state_watermark=max(int(payload["state_watermark"]), 0),
            order_size=max(int(payload["order_size"]), 0),
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _revision_matches(session_dir: Path, revision: _RunRevision) -> bool:
    return (
        (session_dir / RUN_ORDER_FILE).is_file()
        and revision.state_watermark == _state_watermark(session_dir)
        and revision.order_size == _file_size(session_dir / RUN_ORDER_FILE)
    )


def _state_watermark(session_dir: Path) -> int:
    state_dir = session_dir / RUN_RECORDS_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir.stat().st_mtime_ns


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return payload


def _write_authoritative_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{new_id('tmp')}")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(redact_for_storage(dict(payload)), handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _write_derived_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{new_id('tmp')}")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(dict(payload), handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp_path, path)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0
