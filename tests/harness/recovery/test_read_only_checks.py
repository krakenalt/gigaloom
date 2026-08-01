from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from gigaloom.diagnostics.recovery import (
    RecoveryCheckService,
    RecoveryCheckStatus,
    RecoveryScanLimits,
)
from gigaloom.runtime.db import RUNTIME_SCHEMA_VERSION


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def test_read_only_service_checks_runtime_sessions_attachments_and_indexes(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    _runtime_database(root / "runtime.sqlite3")
    session_dir = _session_dir(root)
    blob = root / "projects" / "default" / "attachments" / ("a" * 64) / "original"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"bounded attachment")
    blob_digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    _write_json(
        blob.parent / "metadata.json",
        {"sha256": blob_digest, "size_bytes": blob.stat().st_size},
    )
    _write_jsonl(
        session_dir / "attachments.jsonl",
        [
            {
                "id": "att-one",
                "session_id": "session-one",
                "storage_path": str(blob),
                "size_bytes": blob.stat().st_size,
                "sha256": blob_digest,
            }
        ],
    )
    _write_jsonl(
        session_dir / "events.jsonl",
        [
            _event("event-one", sequence=1, event_type="run_started"),
            _event("event-two", sequence=2, event_type="run_finished"),
        ],
    )
    _write_run_state(session_dir, status="succeeded")
    _derived_read_index(root / "sessions" / "read_model.sqlite3", complete=True)
    before = _tree_digest(root)

    report = RecoveryCheckService(clock=lambda: NOW).check(root)

    assert not report.failed
    assert before == _tree_digest(root)
    by_kind = {item.kind: item for item in report.checks}
    assert {
        "attachment_consistency",
        "derived_index_revision",
        "idempotency_uniqueness",
        "jsonl_integrity",
        "lease_integrity",
        "sqlite_integrity",
        "sqlite_schema",
        "terminal_event_consistency",
    } <= set(by_kind)
    assert by_kind["attachment_consistency"].records_checked == 1
    assert by_kind["terminal_event_consistency"].status is RecoveryCheckStatus.PASSED
    assert all(not item.source_ref.startswith("/") for item in report.checks)


def test_runtime_schema_lease_and_idempotency_fail_closed(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    path = root / "runtime.sqlite3"
    _runtime_database(
        path,
        schema_version=RUNTIME_SCHEMA_VERSION - 1,
        active_lease=(None, None),
        duplicate_idempotency=True,
    )

    report = RecoveryCheckService(clock=lambda: NOW).check(root)
    reasons = {item.reason_code for item in report.checks}

    assert report.failed
    assert "runtime_schema_mismatch" in reasons
    assert "active_lease_invalid" in reasons
    assert "idempotency_duplicate" in reasons


def test_expired_owned_lease_is_warning_not_corruption(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    _runtime_database(
        root / "runtime.sqlite3",
        active_lease=("worker-one", (NOW - timedelta(seconds=1)).isoformat()),
    )

    report = RecoveryCheckService(clock=lambda: NOW).check(root)
    lease = next(item for item in report.checks if item.kind == "lease_integrity")

    assert lease.status is RecoveryCheckStatus.WARNING
    assert lease.reason_code == "active_lease_expired"


def test_jsonl_sequence_owner_and_parse_failures_are_distinct(tmp_path):
    root = tmp_path / "state"
    session_dir = _session_dir(root)
    events = session_dir / "events.jsonl"
    _write_jsonl(
        events,
        [
            _event("event-one", sequence=1),
            _event("event-two", sequence=3),
        ],
    )
    report = RecoveryCheckService(clock=lambda: NOW).check(root)
    assert "jsonl_sequence_gap" in {item.reason_code for item in report.checks}

    _write_jsonl(events, [{**_event("event-one", sequence=1), "session_id": "other"}])
    report = RecoveryCheckService(clock=lambda: NOW).check(root)
    assert "jsonl_owner_mismatch" in {item.reason_code for item in report.checks}

    events.write_text('{"id":', encoding="utf-8")
    report = RecoveryCheckService(clock=lambda: NOW).check(root)
    assert "jsonl_invalid" in {item.reason_code for item in report.checks}


def test_attachment_and_terminal_digests_detect_drift(tmp_path):
    root = tmp_path / "state"
    session_dir = _session_dir(root)
    blob = root / "projects" / "default" / "attachments" / "digest" / "original"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"changed")
    _write_json(blob.parent / "metadata.json", {"sha256": "0" * 64, "size_bytes": 7})
    _write_jsonl(
        session_dir / "attachments.jsonl",
        [
            {
                "id": "att-one",
                "session_id": "session-one",
                "storage_path": str(blob),
                "size_bytes": 7,
                "sha256": "0" * 64,
            }
        ],
    )
    _write_jsonl(
        session_dir / "events.jsonl",
        [_event("event-one", sequence=1, event_type="run_started")],
    )
    _write_run_state(session_dir, status="succeeded")

    report = RecoveryCheckService(clock=lambda: NOW).check(root)
    reasons = {item.reason_code for item in report.checks}

    assert "attachment_digest_mismatch" in reasons
    assert "terminal_event_missing" in reasons


def test_scan_and_record_limits_are_explicit_and_deterministic(tmp_path):
    root = tmp_path / "state"
    session_dir = _session_dir(root)
    _write_jsonl(
        session_dir / "messages.jsonl",
        [{"id": f"message-{index}", "session_id": "session-one"} for index in range(5)],
    )
    for index in range(4):
        _write_json(root / f"extra-{index}.json", {"index": index})
    limits = RecoveryScanLimits(max_files=2, max_records=2)

    first = RecoveryCheckService(limits=limits, clock=lambda: NOW).check(root)
    second = RecoveryCheckService(limits=limits, clock=lambda: NOW).check(root)

    assert first.checks == second.checks
    assert first.files_observed <= limits.max_files
    assert any(item.reason_code == "scan_limit_reached" for item in first.checks)


def test_large_file_is_skipped_before_content_hashing(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    oversized = root / "records.jsonl"
    oversized.write_text("{}\n{}\n", encoding="utf-8")
    limits = RecoveryScanLimits(max_file_bytes=2)

    report = RecoveryCheckService(limits=limits, clock=lambda: NOW).check(root)
    result = next(item for item in report.checks if item.kind == "jsonl_integrity")

    assert result.status is RecoveryCheckStatus.SKIPPED
    assert result.reason_code == "file_size_limit"
    assert result.source_digest is None


def _runtime_database(
    path: Path,
    *,
    schema_version: int = RUNTIME_SCHEMA_VERSION,
    active_lease: tuple[str | None, str | None] | None = (
        "worker-one",
        (NOW + timedelta(minutes=5)).isoformat(),
    ),
    duplicate_idempotency: bool = False,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                origin TEXT NOT NULL,
                idempotency_key_hash TEXT NOT NULL
            );
            CREATE TABLE job_attempts (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                lease_owner TEXT,
                leased_until TEXT
            );
            """
        )
        connection.execute(f"PRAGMA user_version = {schema_version}")
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (schema_version, "fixture", NOW.isoformat()),
        )
        connection.execute("INSERT INTO jobs VALUES ('job-one', 'cli', 'key')")
        if duplicate_idempotency:
            connection.execute("INSERT INTO jobs VALUES ('job-two', 'cli', 'key')")
        if active_lease is not None:
            connection.execute(
                "INSERT INTO job_attempts VALUES ('attempt-one', 'running', ?, ?)",
                active_lease,
            )


def _derived_read_index(path: Path, *, complete: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE read_index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        for key, value in (
            ("generation", "1"),
            ("run_generation", "1"),
            ("complete", "1" if complete else "0"),
            ("records_complete", "1" if complete else "0"),
        ):
            connection.execute(
                "INSERT INTO read_index_meta VALUES (?, ?)", (key, value)
            )


def _session_dir(root: Path) -> Path:
    path = root / "sessions" / "2026" / "08" / "session-one"
    path.mkdir(parents=True, exist_ok=True)
    _write_json(path / "manifest.json", {"id": "session-one"})
    return path


def _event(
    event_id: str,
    *,
    sequence: int,
    event_type: str = "delta",
) -> dict[str, object]:
    return {
        "id": event_id,
        "session_id": "session-one",
        "run_id": "run-one",
        "trace_id": "trace-one",
        "sequence": sequence,
        "type": event_type,
    }


def _write_run_state(session_dir: Path, *, status: str) -> None:
    _write_json(
        session_dir / "run_records" / "run-one.json",
        {
            "schema_version": 1,
            "position": 0,
            "run": {"id": "run-one", "status": status},
        },
    )


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
