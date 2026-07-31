"""Bounded read-only checks for runtime and session persistence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from urllib.parse import quote

from gigaloom.diagnostics.recovery.models import (
    RecoveryCheckResult,
    RecoveryCheckStatus,
    RecoveryScanLimits,
)
from gigaloom.runtime.db import RUNTIME_SCHEMA_VERSION


_ACTIVE_ATTEMPT_STATUSES = ("claimed", "starting", "running")
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "canceled"})
_TERMINAL_EVENT_TYPE = "run_finished"
_SUPPORTED_SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})


@dataclass(frozen=True, slots=True)
class FileObservation:
    """One safe, bounded source selected for checking."""

    path: Path
    source_ref: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class JsonlObservation:
    """Parsed JSONL metadata retained without record bodies."""

    result: RecoveryCheckResult
    records: tuple[Mapping[str, Any], ...]


def check_sqlite(
    observation: FileObservation,
    *,
    limits: RecoveryScanLimits,
    now: datetime,
) -> tuple[RecoveryCheckResult, ...]:
    """Check SQLite structure and known runtime invariants without writes."""
    if observation.size_bytes > limits.max_sqlite_bytes:
        return (
            _result(
                observation,
                kind="sqlite_integrity",
                status=RecoveryCheckStatus.SKIPPED,
                reason="sqlite_size_limit",
                source_digest=None,
            ),
        )
    source_digest = _sha256_file(observation.path)
    try:
        with _read_only_sqlite(
            observation.path,
            timeout_seconds=limits.sqlite_timeout_seconds,
        ) as connection:
            quick = tuple(
                str(row[0]) for row in connection.execute("PRAGMA quick_check")
            )
            integrity = tuple(
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            )
            integrity_status = (
                RecoveryCheckStatus.PASSED
                if quick == ("ok",) and integrity == ("ok",)
                else RecoveryCheckStatus.FAILED
            )
            results = [
                _result(
                    observation,
                    kind="sqlite_integrity",
                    status=integrity_status,
                    reason=(
                        "sqlite_integrity_ok"
                        if integrity_status is RecoveryCheckStatus.PASSED
                        else "sqlite_integrity_failed"
                    ),
                    source_digest=source_digest,
                )
            ]
            tables = _sqlite_tables(connection)
            if observation.path.name == "runtime.sqlite3":
                results.extend(
                    _runtime_sqlite_checks(
                        connection,
                        observation,
                        tables=tables,
                        limits=limits,
                        now=now,
                        source_digest=source_digest,
                    )
                )
            results.append(
                _derived_index_check(
                    connection,
                    observation,
                    tables=tables,
                    source_digest=source_digest,
                )
            )
            return tuple(results)
    except (OSError, sqlite3.DatabaseError) as exc:
        return (
            _result(
                observation,
                kind="sqlite_integrity",
                status=RecoveryCheckStatus.FAILED,
                reason=_sqlite_failure_reason(exc),
                source_digest=source_digest,
            ),
        )


def check_json(
    observation: FileObservation,
    *,
    limits: RecoveryScanLimits,
) -> RecoveryCheckResult:
    """Validate one bounded JSON object or array."""
    if observation.size_bytes > limits.max_file_bytes:
        return _result(
            observation,
            kind="json_parse",
            status=RecoveryCheckStatus.SKIPPED,
            reason="file_size_limit",
            source_digest=None,
        )
    source_digest = _sha256_file(observation.path)
    try:
        with observation.path.open("r", encoding="utf-8") as handle:
            json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _result(
            observation,
            kind="json_parse",
            status=RecoveryCheckStatus.FAILED,
            reason="json_invalid",
            source_digest=source_digest,
        )
    return _result(
        observation,
        kind="json_parse",
        status=RecoveryCheckStatus.PASSED,
        reason="json_valid",
        records=1,
        source_digest=source_digest,
    )


def check_jsonl(
    observation: FileObservation,
    *,
    data_root: Path,
    limits: RecoveryScanLimits,
) -> JsonlObservation:
    """Validate bounded JSONL framing, identity, sequence, and digests."""
    if observation.size_bytes > limits.max_file_bytes:
        return JsonlObservation(
            _result(
                observation,
                kind="jsonl_integrity",
                status=RecoveryCheckStatus.SKIPPED,
                reason="file_size_limit",
                source_digest=None,
            ),
            (),
        )
    source_digest = _sha256_file(observation.path)
    records: list[Mapping[str, Any]] = []
    omitted = 0
    failure: str | None = None
    sequences: dict[str, int] = {}
    ids: set[str] = set()
    expected_owner = _session_owner(observation.path, data_root)
    try:
        with observation.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                if len(records) >= limits.max_records:
                    omitted = 1
                    break
                if len(line.encode("utf-8")) > limits.max_file_bytes:
                    failure = "jsonl_line_size_limit"
                    break
                decoded = json.loads(line)
                if not isinstance(decoded, Mapping):
                    failure = "jsonl_record_not_object"
                    break
                record = dict(decoded)
                record_id = record.get("id")
                if isinstance(record_id, str):
                    if record_id in ids:
                        failure = "jsonl_duplicate_id"
                        break
                    ids.add(record_id)
                owner = record.get("session_id")
                if expected_owner is not None and owner != expected_owner:
                    failure = "jsonl_owner_mismatch"
                    break
                sequence = record.get("sequence")
                if sequence is not None:
                    if not isinstance(sequence, int) or isinstance(sequence, bool):
                        failure = "jsonl_sequence_invalid"
                        break
                    sequence_owner = str(record.get("trace_id") or owner or "default")
                    previous = sequences.get(sequence_owner)
                    if previous is not None and sequence != previous + 1:
                        failure = "jsonl_sequence_gap"
                        break
                    sequences[sequence_owner] = sequence
                records.append(record)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        failure = "jsonl_invalid"
    status = RecoveryCheckStatus.FAILED if failure else RecoveryCheckStatus.PASSED
    reason = failure or ("jsonl_record_limit" if omitted else "jsonl_valid")
    if omitted and failure is None:
        status = RecoveryCheckStatus.WARNING
    return JsonlObservation(
        _result(
            observation,
            kind="jsonl_integrity",
            status=status,
            reason=reason,
            records=len(records),
            omitted=omitted,
            source_digest=source_digest,
        ),
        tuple(records),
    )


def attachment_consistency_check(
    observation: FileObservation,
    records: Iterable[Mapping[str, Any]],
    *,
    data_root: Path,
) -> RecoveryCheckResult:
    """Bind attachment records to bounded metadata and blob digests."""
    records_tuple = tuple(records)
    failure = _attachment_failure(records_tuple, data_root)
    return _result(
        observation,
        kind="attachment_consistency",
        status=(RecoveryCheckStatus.FAILED if failure else RecoveryCheckStatus.PASSED),
        reason=failure or "attachments_consistent",
        records=len(records_tuple),
        source_digest=_sha256_file(observation.path),
    )


def terminal_event_check(
    observation: FileObservation,
    records: Iterable[Mapping[str, Any]],
    *,
    limits: RecoveryScanLimits,
) -> RecoveryCheckResult:
    """Match terminal run state to exactly one final event."""
    terminal_counts: dict[str, int] = defaultdict(int)
    for record in records:
        run_id = record.get("run_id")
        if isinstance(run_id, str) and record.get("type") == _TERMINAL_EVENT_TYPE:
            terminal_counts[run_id] += 1
    failure = next(
        ("duplicate_terminal_event" for count in terminal_counts.values() if count > 1),
        None,
    )
    checked = len(terminal_counts)
    if failure is None:
        state_dir = observation.path.parent / "run_records"
        for state_path in sorted(state_dir.glob("*.json"))[: limits.max_records]:
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                run = payload["run"]
                run_id = str(run["id"])
                status = str(run["status"])
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                failure = "run_state_invalid"
                break
            if status in _TERMINAL_RUN_STATUSES and terminal_counts.get(run_id) != 1:
                failure = "terminal_event_missing"
                break
            if status not in _TERMINAL_RUN_STATUSES and terminal_counts.get(run_id, 0):
                failure = "terminal_event_for_active_run"
                break
            checked += 1
    return _result(
        observation,
        kind="terminal_event_consistency",
        status=(RecoveryCheckStatus.FAILED if failure else RecoveryCheckStatus.PASSED),
        reason=failure or "terminal_events_consistent",
        records=checked,
        source_digest=_sha256_file(observation.path),
    )


def _runtime_sqlite_checks(
    connection: sqlite3.Connection,
    observation: FileObservation,
    *,
    tables: frozenset[str],
    limits: RecoveryScanLimits,
    now: datetime,
    source_digest: str,
) -> tuple[RecoveryCheckResult, ...]:
    results: list[RecoveryCheckResult] = []
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    required_tables = {"schema_migrations", "jobs", "job_attempts"}
    schema_ok = version == RUNTIME_SCHEMA_VERSION and required_tables <= tables
    results.append(
        _result(
            observation,
            kind="sqlite_schema",
            status=(
                RecoveryCheckStatus.PASSED if schema_ok else RecoveryCheckStatus.FAILED
            ),
            reason="runtime_schema_current" if schema_ok else "runtime_schema_mismatch",
            source_digest=source_digest,
        )
    )
    if "job_attempts" in tables:
        placeholders = ",".join("?" for _ in _ACTIVE_ATTEMPT_STATUSES)
        rows = connection.execute(
            f"""
            SELECT lease_owner, leased_until
            FROM job_attempts
            WHERE status IN ({placeholders})
            ORDER BY id LIMIT ?
            """,
            (*_ACTIVE_ATTEMPT_STATUSES, limits.max_records + 1),
        ).fetchall()
        bounded = rows[: limits.max_records]
        invalid = sum(
            1
            for row in bounded
            if not row[0] or not row[1] or _parse_time(str(row[1])) is None
        )
        expired = sum(
            1
            for row in bounded
            if (parsed := _parse_time(str(row[1]))) is not None and parsed < now
        )
        status = RecoveryCheckStatus.PASSED
        reason = "active_leases_valid"
        if invalid:
            status = RecoveryCheckStatus.FAILED
            reason = "active_lease_invalid"
        elif expired:
            status = RecoveryCheckStatus.WARNING
            reason = "active_lease_expired"
        elif len(rows) > limits.max_records:
            status = RecoveryCheckStatus.WARNING
            reason = "lease_record_limit"
        results.append(
            _result(
                observation,
                kind="lease_integrity",
                status=status,
                reason=reason,
                records=len(bounded),
                omitted=max(len(rows) - len(bounded), 0),
                source_digest=source_digest,
            )
        )
    if "jobs" in tables:
        duplicate = connection.execute(
            """
            SELECT 1 FROM jobs
            GROUP BY origin, idempotency_key_hash HAVING COUNT(*) > 1 LIMIT 1
            """
        ).fetchone()
        results.append(
            _result(
                observation,
                kind="idempotency_uniqueness",
                status=(
                    RecoveryCheckStatus.FAILED
                    if duplicate
                    else RecoveryCheckStatus.PASSED
                ),
                reason=("idempotency_duplicate" if duplicate else "idempotency_unique"),
                source_digest=source_digest,
            )
        )
    return tuple(results)


def _derived_index_check(
    connection: sqlite3.Connection,
    observation: FileObservation,
    *,
    tables: frozenset[str],
    source_digest: str,
) -> RecoveryCheckResult:
    if "read_index_meta" in tables:
        meta = dict(connection.execute("SELECT key, value FROM read_index_meta"))
        complete = meta.get("complete") == "1" and meta.get("records_complete") == "1"
        return _result(
            observation,
            kind="derived_index_revision",
            status=(
                RecoveryCheckStatus.PASSED if complete else RecoveryCheckStatus.WARNING
            ),
            reason="derived_index_complete" if complete else "derived_index_incomplete",
            records=len(meta),
            source_digest=source_digest,
        )
    if "session_catalog_meta" in tables:
        meta = dict(connection.execute("SELECT key, value FROM session_catalog_meta"))
        complete = meta.get("complete") == "1" and bool(meta.get("watermark"))
        return _result(
            observation,
            kind="derived_index_revision",
            status=(
                RecoveryCheckStatus.PASSED if complete else RecoveryCheckStatus.WARNING
            ),
            reason="derived_index_complete" if complete else "derived_index_incomplete",
            records=len(meta),
            source_digest=source_digest,
        )
    return _result(
        observation,
        kind="derived_index_revision",
        status=RecoveryCheckStatus.SKIPPED,
        reason="not_a_known_derived_index",
        source_digest=source_digest,
    )


def _attachment_failure(
    records: Iterable[Mapping[str, Any]],
    data_root: Path,
) -> str | None:
    resolved_root = data_root.resolve()
    for record in records:
        storage_path = record.get("storage_path")
        if storage_path is None:
            continue
        try:
            blob = Path(str(storage_path)).expanduser().resolve(strict=True)
            blob.relative_to(resolved_root)
        except (OSError, ValueError):
            return "attachment_blob_missing_or_escaped"
        if not blob.is_file():
            return "attachment_blob_missing_or_escaped"
        expected_size = record.get("size_bytes")
        if not isinstance(expected_size, int) or blob.stat().st_size != expected_size:
            return "attachment_size_mismatch"
        expected_digest = record.get("sha256")
        if (
            not isinstance(expected_digest, str)
            or _sha256_file(blob) != expected_digest
        ):
            return "attachment_digest_mismatch"
        metadata_path = blob.parent / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "attachment_metadata_missing_or_invalid"
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("sha256") != expected_digest
            or metadata.get("size_bytes") != expected_size
        ):
            return "attachment_metadata_mismatch"
    return None


@contextmanager
def _read_only_sqlite(
    path: Path,
    *,
    timeout_seconds: float,
) -> Iterator[sqlite3.Connection]:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=timeout_seconds)
    connection.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)}")
    connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
    finally:
        connection.close()


def _sqlite_tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    )


def _session_owner(path: Path, data_root: Path) -> str | None:
    try:
        relative = path.resolve().relative_to((data_root / "sessions").resolve())
    except ValueError:
        return None
    if len(relative.parts) >= 4:
        return relative.parts[-2]
    return None


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        digest.update(b"unreadable")
    return digest.hexdigest()


def _result(
    observation: FileObservation,
    *,
    kind: str,
    status: RecoveryCheckStatus,
    reason: str,
    records: int = 0,
    omitted: int = 0,
    source_digest: str | None,
) -> RecoveryCheckResult:
    identity = {
        "kind": kind,
        "source_ref": observation.source_ref,
        "status": status.value,
        "reason": reason,
        "records_checked": records,
        "records_omitted": omitted,
        "source_digest": source_digest,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    evidence_digest = hashlib.sha256(encoded).hexdigest()
    check_id = hashlib.sha256(f"{kind}\0{observation.source_ref}".encode()).hexdigest()[
        :24
    ]
    return RecoveryCheckResult(
        check_id=f"check-{check_id}",
        kind=kind,
        source_ref=observation.source_ref,
        status=status,
        reason_code=reason,
        records_checked=records,
        records_omitted=omitted,
        evidence_digest=evidence_digest,
        source_digest=source_digest,
    )


def _sqlite_failure_reason(exc: BaseException) -> str:
    message = str(exc).casefold()
    if "locked" in message or "busy" in message:
        return "sqlite_locked"
    if "not a database" in message or "malformed" in message:
        return "sqlite_corrupt"
    return "sqlite_unreadable"
