from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from gigaloom.diagnostics.recovery import (
    RecoveryActionKind,
    RecoveryActionStatus,
    RecoveryCheckService,
    RecoveryScanLimits,
)


NOW = datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc)


def test_preview_reports_all_known_derived_index_drift_without_writes(tmp_path):
    root = tmp_path / "state"
    session_dir = _authoritative_session(root)
    _stale_catalog(root / "sessions" / "catalog.sqlite3")
    _stale_read_model(root / "sessions" / "read_model.sqlite3")
    _write_json(
        root / "attachments" / "index.json",
        {"attachments": [{"id": "stale", "session_id": "session-old"}]},
    )
    _write_jsonl(
        session_dir / "run_order.jsonl",
        [{"id": "run-stale", "position": 0}],
    )
    before = _tree_digest(root)

    preview = RecoveryCheckService(clock=lambda: NOW).preview(root)

    assert before == _tree_digest(root)
    rebuilds = {item.target_ref: item for item in preview.rebuilds}
    assert {
        "attachments/index.json",
        "sessions/catalog.sqlite3",
        "sessions/read_model.sqlite3",
        "sessions/2026/08/session-one/run_order.jsonl",
    } <= set(rebuilds)
    assert all(
        item.status is RecoveryActionStatus.RECOMMENDED for item in rebuilds.values()
    )
    assert all(
        item.kind is RecoveryActionKind.REBUILD_DERIVED_INDEX
        and item.backup_required
        and item.explicit_command_required
        for item in rebuilds.values()
    )
    assert rebuilds["attachments/index.json"].candidate_records == 1
    assert rebuilds["sessions/catalog.sqlite3"].candidate_records == 1
    assert rebuilds["sessions/read_model.sqlite3"].candidate_records == 2


def test_current_projection_is_noop_but_still_requires_explicit_mutation_contract(
    tmp_path,
):
    root = tmp_path / "state"
    session_dir = _authoritative_session(root)
    _write_json(
        root / "attachments" / "index.json",
        {"attachments": [{"id": "att-one", "session_id": "session-one"}]},
    )
    _write_jsonl(session_dir / "run_order.jsonl", [{"id": "run-one", "position": 0}])

    preview = RecoveryCheckService(clock=lambda: NOW).preview(root)
    by_target = {item.target_ref: item for item in preview.rebuilds}

    assert by_target["attachments/index.json"].status is RecoveryActionStatus.NOT_NEEDED
    assert (
        by_target["sessions/2026/08/session-one/run_order.jsonl"].status
        is RecoveryActionStatus.NOT_NEEDED
    )
    assert not hasattr(RecoveryCheckService, "apply")


def test_corrupt_authoritative_record_produces_content_free_quarantine_preview(
    tmp_path,
):
    root = tmp_path / "state"
    session_dir = _authoritative_session(root)
    corrupt = session_dir / "messages.jsonl"
    secret = "super-secret-record-content"
    corrupt.write_text(f'{{"secret":"{secret}"', encoding="utf-8")
    before = _tree_digest(root)

    preview = RecoveryCheckService(clock=lambda: NOW).preview(root)

    assert before == _tree_digest(root)
    assert len(preview.quarantines) == 1
    action = preview.quarantines[0]
    assert action.kind is RecoveryActionKind.QUARANTINE_RECORD
    assert action.status is RecoveryActionStatus.RECOMMENDED
    assert action.target_ref.endswith("messages.jsonl")
    assert action.backup_required and action.explicit_command_required
    assert secret not in repr(action)


def test_incomplete_bounded_scan_blocks_rebuild_preview(tmp_path):
    root = tmp_path / "state"
    _authoritative_session(root)
    _stale_catalog(root / "sessions" / "catalog.sqlite3")
    limits = RecoveryScanLimits(max_files=2)

    preview = RecoveryCheckService(limits=limits, clock=lambda: NOW).preview(root)

    assert any(item.status is RecoveryActionStatus.BLOCKED for item in preview.rebuilds)
    assert all(item.backup_required for item in preview.rebuilds)


def _authoritative_session(root: Path) -> Path:
    session_dir = root / "sessions" / "2026" / "08" / "session-one"
    _write_json(session_dir / "manifest.json", {"id": "session-one"})
    _write_json(
        session_dir / "run_records" / "run-one.json",
        {
            "schema_version": 1,
            "position": 0,
            "run": {
                "id": "run-one",
                "session_id": "session-one",
                "status": "running",
            },
        },
    )
    _write_jsonl(
        session_dir / "attachments.jsonl",
        [{"id": "att-one", "session_id": "session-one"}],
    )
    return session_dir


def _stale_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE session_catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE session_catalog_entries (session_id TEXT, relative_path TEXT)"
        )
        for key, value in (("complete", "1"), ("watermark", "stale")):
            connection.execute(
                "INSERT INTO session_catalog_meta VALUES (?, ?)", (key, value)
            )


def _stale_read_model(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE read_index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE read_index_sessions (id TEXT PRIMARY KEY);
            CREATE TABLE read_index_runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                position INTEGER NOT NULL
            );
            """
        )
        for key in ("complete", "records_complete"):
            connection.execute("INSERT INTO read_index_meta VALUES (?, '1')", (key,))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
