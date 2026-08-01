"""Read-only previews for deterministic derived-index rebuilds."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from gigaloom.diagnostics.recovery.checks import read_only_sqlite
from gigaloom.diagnostics.recovery.models import (
    RecoveryActionKind,
    RecoveryActionPreview,
    RecoveryActionStatus,
    RecoveryCheckStatus,
    RecoveryScanLimits,
    RecoveryScanReport,
)


def preview_derived_rebuilds(
    root: Path,
    scan: RecoveryScanReport,
    *,
    limits: RecoveryScanLimits,
) -> tuple[RecoveryActionPreview, ...]:
    """Compare known derived projections with bounded authoritative sources."""
    source_refs = tuple(
        sorted(
            {
                item.source_ref
                for item in scan.checks
                if item.kind in {"json_parse", "jsonl_integrity"}
            }
        )
    )
    invalid_refs = {
        item.source_ref
        for item in scan.checks
        if item.status in {RecoveryCheckStatus.FAILED, RecoveryCheckStatus.SKIPPED}
    }
    limited = any(item.reason_code == "scan_limit_reached" for item in scan.checks)
    previews = [
        *_session_catalog_preview(
            root,
            source_refs,
            invalid_refs=invalid_refs,
            limited=limited,
            limits=limits,
        ),
        *_read_model_preview(
            root,
            source_refs,
            invalid_refs=invalid_refs,
            limited=limited,
            limits=limits,
        ),
        *_attachment_index_preview(
            root,
            source_refs,
            invalid_refs=invalid_refs,
            limited=limited,
            limits=limits,
        ),
        *_run_order_previews(
            root,
            source_refs,
            invalid_refs=invalid_refs,
            limited=limited,
            limits=limits,
        ),
    ]
    return tuple(sorted(previews, key=lambda item: item.action_id))


def _session_catalog_preview(
    root: Path,
    source_refs: tuple[str, ...],
    *,
    invalid_refs: set[str],
    limited: bool,
    limits: RecoveryScanLimits,
) -> tuple[RecoveryActionPreview, ...]:
    target = "sessions/catalog.sqlite3"
    manifests = tuple(
        ref
        for ref in source_refs
        if ref.startswith("sessions/") and ref.endswith("/manifest.json")
    )
    if not manifests and not (root / target).exists():
        return ()
    blocked = limited or any(ref in invalid_refs for ref in manifests)
    expected: list[tuple[str, str]] = []
    if not blocked:
        try:
            for ref in manifests[: limits.max_records]:
                payload = _json_object(root / ref)
                expected.append((str(payload["id"]), str(Path(ref).parent)))
            if len(manifests) > limits.max_records:
                blocked = True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            blocked = True
    current, current_digest, current_blocked = _sqlite_rows(
        root / target,
        "SELECT session_id, relative_path FROM session_catalog_entries ORDER BY session_id",
        limits=limits,
    )
    return (
        _preview(
            target,
            expected=expected,
            current=current,
            blocked=blocked or current_blocked,
            source_digest=current_digest,
        ),
    )


def _read_model_preview(
    root: Path,
    source_refs: tuple[str, ...],
    *,
    invalid_refs: set[str],
    limited: bool,
    limits: RecoveryScanLimits,
) -> tuple[RecoveryActionPreview, ...]:
    target = "sessions/read_model.sqlite3"
    manifests = tuple(ref for ref in source_refs if ref.endswith("/manifest.json"))
    run_states = tuple(ref for ref in source_refs if "/run_records/" in ref)
    if not manifests and not run_states and not (root / target).exists():
        return ()
    blocked = limited or any(ref in invalid_refs for ref in (*manifests, *run_states))
    expected: list[tuple[object, ...]] = []
    if not blocked:
        try:
            for ref in manifests[: limits.max_records]:
                expected.append(("session", str(_json_object(root / ref)["id"])))
            for ref in run_states[: limits.max_records]:
                payload = _json_object(root / ref)
                run = payload["run"]
                if not isinstance(run, Mapping):
                    raise ValueError("run state is invalid")
                expected.append(
                    (
                        "run",
                        str(run["id"]),
                        str(run["session_id"]),
                        int(payload["position"]),
                    )
                )
            if (
                len(manifests) > limits.max_records
                or len(run_states) > limits.max_records
            ):
                blocked = True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            blocked = True
    sessions, session_digest, sessions_blocked = _sqlite_rows(
        root / target,
        "SELECT 'session', id FROM read_index_sessions ORDER BY id",
        limits=limits,
    )
    runs, run_digest, runs_blocked = _sqlite_rows(
        root / target,
        "SELECT 'run', id, session_id, position FROM read_index_runs ORDER BY id",
        limits=limits,
    )
    return (
        _preview(
            target,
            expected=expected,
            current=[*sessions, *runs],
            blocked=blocked or sessions_blocked or runs_blocked,
            source_digest=_canonical_digest((session_digest, run_digest)),
        ),
    )


def _attachment_index_preview(
    root: Path,
    source_refs: tuple[str, ...],
    *,
    invalid_refs: set[str],
    limited: bool,
    limits: RecoveryScanLimits,
) -> tuple[RecoveryActionPreview, ...]:
    target = "attachments/index.json"
    sources = tuple(ref for ref in source_refs if ref.endswith("/attachments.jsonl"))
    if not sources and not (root / target).exists():
        return ()
    blocked = limited or any(ref in invalid_refs for ref in sources)
    expected: list[tuple[str, str]] = []
    if not blocked:
        try:
            for ref in sources:
                for record in _jsonl_objects(root / ref, limit=limits.max_records):
                    expected.append((str(record["id"]), str(record["session_id"])))
                    if len(expected) > limits.max_records:
                        blocked = True
                        break
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            blocked = True
    current: list[tuple[str, str]] = []
    current_digest: str | None = None
    path = root / target
    if path.exists():
        try:
            if path.stat().st_size > limits.max_file_bytes:
                blocked = True
            else:
                current_digest = _file_digest(path)
        except OSError:
            blocked = True
        try:
            payload = _json_object(path) if not blocked else {}
            entries = payload.get("attachments", [])
            if not isinstance(entries, list) or len(entries) > limits.max_records:
                blocked = True
            else:
                current = [
                    (str(item["id"]), str(item["session_id"]))
                    for item in entries
                    if isinstance(item, Mapping)
                ]
                if len(current) != len(entries):
                    blocked = True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            current = []
    return (
        _preview(
            target,
            expected=expected,
            current=current,
            blocked=blocked,
            source_digest=current_digest,
        ),
    )


def _run_order_previews(
    root: Path,
    source_refs: tuple[str, ...],
    *,
    invalid_refs: set[str],
    limited: bool,
    limits: RecoveryScanLimits,
) -> tuple[RecoveryActionPreview, ...]:
    state_dirs = {
        str(Path(ref).parent) for ref in source_refs if "/run_records/" in ref
    }
    state_dirs.update(
        str(Path(ref).parent / "run_records")
        for ref in source_refs
        if ref.endswith("/run_order.jsonl")
    )
    previews: list[RecoveryActionPreview] = []
    for state_dir in sorted(state_dirs):
        state_refs = tuple(
            ref for ref in source_refs if str(Path(ref).parent) == state_dir
        )
        target = str(Path(state_dir).parent / "run_order.jsonl")
        blocked = limited or any(ref in invalid_refs for ref in state_refs)
        expected: list[tuple[int, str]] = []
        try:
            for ref in state_refs[: limits.max_records]:
                payload = _json_object(root / ref)
                run = payload["run"]
                if not isinstance(run, Mapping):
                    raise ValueError("run state is invalid")
                expected.append((int(payload["position"]), str(run["id"])))
            if len(state_refs) > limits.max_records:
                blocked = True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            blocked = True
        current: list[tuple[int, str]] = []
        path = root / target
        current_digest: str | None = None
        if path.exists():
            try:
                if path.stat().st_size > limits.max_file_bytes:
                    blocked = True
                else:
                    current_digest = _file_digest(path)
            except OSError:
                blocked = True
            try:
                current = [
                    (int(item["position"]), str(item["id"]))
                    for item in (
                        _jsonl_objects(path, limit=limits.max_records)
                        if not blocked
                        else ()
                    )
                ]
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                current = []
        previews.append(
            _preview(
                target,
                expected=expected,
                current=current,
                blocked=blocked,
                source_digest=current_digest,
            )
        )
    return tuple(previews)


def _sqlite_rows(
    path: Path,
    query: str,
    *,
    limits: RecoveryScanLimits,
) -> tuple[list[tuple[object, ...]], str | None, bool]:
    if not path.exists():
        return [], None, False
    try:
        if path.stat().st_size > limits.max_sqlite_bytes:
            return [], None, True
        digest = _file_digest(path)
    except OSError:
        return [], None, True
    try:
        with read_only_sqlite(
            path,
            timeout_seconds=limits.sqlite_timeout_seconds,
        ) as connection:
            rows = connection.execute(
                f"{query} LIMIT ?", (limits.max_records + 1,)
            ).fetchall()
    except sqlite3.DatabaseError:
        return [], digest, False
    return (
        [tuple(row) for row in rows[: limits.max_records]],
        digest,
        len(rows) > limits.max_records,
    )


def _preview(
    target: str,
    *,
    expected: Iterable[tuple[object, ...]],
    current: Iterable[tuple[object, ...]],
    blocked: bool,
    source_digest: str | None,
) -> RecoveryActionPreview:
    expected_rows = tuple(sorted(expected, key=repr))
    current_rows = tuple(sorted(current, key=repr))
    expected_digest = _canonical_digest(expected_rows)
    if blocked:
        status = RecoveryActionStatus.BLOCKED
        reason = "authoritative_sources_incomplete"
    elif expected_rows == current_rows:
        status = RecoveryActionStatus.NOT_NEEDED
        reason = "derived_index_current"
    else:
        status = RecoveryActionStatus.RECOMMENDED
        reason = "derived_index_drift"
    action_digest = hashlib.sha256(f"rebuild\0{target}".encode()).hexdigest()[:24]
    return RecoveryActionPreview(
        action_id=f"action-{action_digest}",
        kind=RecoveryActionKind.REBUILD_DERIVED_INDEX,
        target_ref=target,
        status=status,
        reason_code=reason,
        source_digest=source_digest,
        expected_digest=expected_digest,
        candidate_records=len(expected_rows),
    )


def _json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def _jsonl_objects(path: Path, *, limit: int) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            if len(records) >= limit:
                raise ValueError("record limit reached")
            decoded = json.loads(line)
            if not isinstance(decoded, dict):
                raise ValueError("expected a JSON object")
            records.append(decoded)
    return tuple(records)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
