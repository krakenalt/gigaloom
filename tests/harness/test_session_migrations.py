from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading

import pytest

from gpt2giga_harness.native import HarnessInvocationMode
from gpt2giga_harness.runtime.models import RunStatus
from gpt2giga_harness.sessions import FilesystemHarnessSessionStore
from gpt2giga_harness.sessions.models import HarnessRun, run_to_dict
import gpt2giga_harness.sessions.storage.filesystem.runs as run_storage
from gpt2giga_harness.types import GigaChatApiMode, HarnessCapability


def test_legacy_runs_migrate_idempotently_and_preserve_order(tmp_path):
    session_id, runs, session_dir = _legacy_store(tmp_path, count=5)

    store = FilesystemHarnessSessionStore(tmp_path)
    assert store.list_runs(session_id) == runs
    first_records = _record_digests(session_dir)
    first_order = (session_dir / run_storage.RUN_ORDER_FILE).read_bytes()

    reopened = FilesystemHarnessSessionStore(tmp_path)
    assert reopened.list_runs(session_id) == runs
    assert _record_digests(session_dir) == first_records
    assert (session_dir / run_storage.RUN_ORDER_FILE).read_bytes() == first_order
    assert (session_dir / run_storage.RUNS_FILE).is_file()
    assert not (session_dir / run_storage.RUN_MIGRATION_MARKER).exists()


def test_legacy_run_migration_restarts_after_interruption(tmp_path, monkeypatch):
    session_id, runs, session_dir = _legacy_store(tmp_path, count=4)
    original_write = run_storage._write_run_state
    writes = 0

    def interrupted_write(path, state):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated migration interruption")
        original_write(path, state)

    monkeypatch.setattr(run_storage, "_write_run_state", interrupted_write)
    with pytest.raises(OSError, match="simulated migration interruption"):
        FilesystemHarnessSessionStore(tmp_path).list_runs(session_id)
    assert (session_dir / run_storage.RUN_MIGRATION_MARKER).is_file()

    monkeypatch.setattr(run_storage, "_write_run_state", original_write)
    reopened = FilesystemHarnessSessionStore(tmp_path)
    assert reopened.list_runs(session_id) == runs
    assert not (session_dir / run_storage.RUN_MIGRATION_MARKER).exists()
    assert len(_record_digests(session_dir)) == len(runs)


def test_run_order_and_sqlite_indexes_rebuild_from_current_state(tmp_path):
    session_id, runs, session_dir = _legacy_store(tmp_path, count=3)
    store = FilesystemHarnessSessionStore(tmp_path)
    assert store.list_runs(session_id) == runs
    assert store.get_run(runs[-1].id) == runs[-1]

    (session_dir / run_storage.RUN_ORDER_FILE).unlink()
    (tmp_path / "sessions" / "read_model.sqlite3").unlink()

    reopened = FilesystemHarnessSessionStore(tmp_path)
    assert reopened.list_runs(session_id) == runs
    assert reopened.get_run(runs[-1].id) == runs[-1]
    assert (session_dir / run_storage.RUN_ORDER_FILE).is_file()


def test_concurrent_run_patches_preserve_previous_fields(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="concurrent patches")
    run = _create_run(store, session.id)
    start = threading.Barrier(2)

    def patch_status():
        start.wait()
        return store.update_run(run.id, status="succeeded")

    def patch_command():
        start.wait()
        return store.update_run(run.id, command=("safe", "command"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(patch_status), executor.submit(patch_command))
        for future in futures:
            future.result()

    updated = store.get_run(run.id)
    assert updated.status is RunStatus.SUCCEEDED
    assert updated.command == ("safe", "command")


def test_run_update_io_is_independent_of_history_size(tmp_path, monkeypatch):
    observations = []
    for scale in (10, 1_000):
        root = tmp_path / f"scale-{scale}"
        session_id, runs, session_dir = _legacy_store(root, count=scale)
        store = FilesystemHarnessSessionStore(root)
        target = runs[-1]
        assert store.get_run(target.id) == target
        legacy_before = (session_dir / run_storage.RUNS_FILE).read_bytes()
        states_before = _record_digests(session_dir)
        reads = 0
        writes = 0
        bytes_written = 0
        original_read = run_storage._read_run_state
        original_write = run_storage._write_run_state

        def counted_read(*args, **kwargs):
            nonlocal reads
            reads += 1
            return original_read(*args, **kwargs)

        def counted_write(path, state):
            nonlocal writes, bytes_written
            writes += 1
            original_write(path, state)
            bytes_written += path.stat().st_size

        with monkeypatch.context() as scoped:
            scoped.setattr(run_storage, "_read_run_state", counted_read)
            scoped.setattr(run_storage, "_write_run_state", counted_write)
            updated = store.update_run(target.id, status="succeeded")

        states_after = _record_digests(session_dir)
        changed = {
            name
            for name, digest in states_after.items()
            if states_before.get(name) != digest
        }
        assert updated.status is RunStatus.SUCCEEDED
        assert reads == 1
        assert writes == 1
        assert changed == {run_storage._run_state_path(session_dir, target.id).name}
        assert (session_dir / run_storage.RUNS_FILE).read_bytes() == legacy_before
        observations.append(bytes_written)

    assert max(observations) <= min(observations) * 1.2


def _legacy_store(
    root: Path,
    *,
    count: int,
) -> tuple[str, tuple[HarnessRun, ...], Path]:
    store = FilesystemHarnessSessionStore(root)
    session = store.create_session(title=f"legacy-{count}")
    session_dir = next((root / "sessions").glob(f"*/*/{session.id}"))
    runs = tuple(_run(session.id, index) for index in range(count))
    with (session_dir / run_storage.RUNS_FILE).open("w", encoding="utf-8") as handle:
        for run in runs:
            handle.write(json.dumps(run_to_dict(run), ensure_ascii=False))
            handle.write("\n")
    return session.id, runs, session_dir


def _create_run(
    store: FilesystemHarnessSessionStore,
    session_id: str,
) -> HarnessRun:
    return store.create_run(
        session_id=session_id,
        harness_id="echo",
        prompt="content-free",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
    )


def _run(session_id: str, index: int) -> HarnessRun:
    return HarnessRun(
        id=f"run_fixture_{index:06d}",
        session_id=session_id,
        harness_id="echo",
        status=RunStatus.QUEUED,
        prompt="content-free",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        invocation_mode=HarnessInvocationMode.HEADLESS,
    )


def _record_digests(session_dir: Path) -> dict[str, str]:
    state_dir = session_dir / run_storage.RUN_RECORDS_DIR
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(state_dir.glob("*.json"))
    }
