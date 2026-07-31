"""Application service for bounded read-only state validation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
from typing import Callable

from gigaloom.diagnostics.recovery.checks import (
    FileObservation,
    attachment_consistency_check,
    check_json,
    check_jsonl,
    check_sqlite,
    terminal_event_check,
)
from gigaloom.diagnostics.recovery.models import (
    RecoveryCheckResult,
    RecoveryCheckStatus,
    RecoveryScanLimits,
    RecoveryScanReport,
)


CHECK_CATALOG = (
    "attachment_consistency",
    "derived_index_revision",
    "idempotency_uniqueness",
    "json_parse",
    "jsonl_integrity",
    "lease_integrity",
    "sqlite_integrity",
    "sqlite_schema",
    "terminal_event_consistency",
)


class RecoveryCheckService:
    """Inspect a data root without creating, migrating, or repairing state."""

    def __init__(
        self,
        *,
        limits: RecoveryScanLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.limits = limits or RecoveryScanLimits()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, data_root: str | Path) -> RecoveryScanReport:
        """Run the stable check catalog over one admitted directory."""
        root = Path(data_root).expanduser().resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("recovery data root must be a real directory")
        observations, files_seen, bytes_seen, limited = _observe_files(
            root,
            limits=self.limits,
        )
        checks: list[RecoveryCheckResult] = []
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("recovery clock must return an aware datetime")
        for observation in observations:
            suffix = observation.path.suffix.casefold()
            if suffix in {".db", ".sqlite", ".sqlite3"}:
                checks.extend(check_sqlite(observation, limits=self.limits, now=now))
            elif suffix == ".jsonl":
                jsonl = check_jsonl(
                    observation,
                    data_root=root,
                    limits=self.limits,
                )
                checks.append(jsonl.result)
                if observation.path.name == "attachments.jsonl" and (
                    jsonl.result.status is RecoveryCheckStatus.PASSED
                ):
                    checks.append(
                        attachment_consistency_check(
                            observation,
                            jsonl.records,
                            data_root=root,
                        )
                    )
                if observation.path.name == "events.jsonl" and (
                    jsonl.result.status is RecoveryCheckStatus.PASSED
                ):
                    checks.append(
                        terminal_event_check(
                            observation,
                            jsonl.records,
                            limits=self.limits,
                        )
                    )
            elif suffix == ".json":
                checks.append(check_json(observation, limits=self.limits))
        if limited:
            checks.append(_limit_result(files_seen, bytes_seen))
        ordered = tuple(sorted(checks, key=lambda item: item.check_id))
        return RecoveryScanReport(
            data_root=root,
            data_root_fingerprint=_root_fingerprint(root, observations, ordered),
            check_catalog_digest=hashlib.sha256(
                "\n".join(CHECK_CATALOG).encode()
            ).hexdigest(),
            checks=ordered,
            files_observed=files_seen,
            bytes_observed=bytes_seen,
        )


def _observe_files(
    root: Path,
    *,
    limits: RecoveryScanLimits,
) -> tuple[tuple[FileObservation, ...], int, int, bool]:
    observations: list[FileObservation] = []
    files_seen = 0
    entries_seen = 0
    bytes_seen = 0
    limited = False
    pending = [root]
    while pending:
        directory = pending.pop()
        remaining = limits.max_files - entries_seen
        if remaining <= 0:
            limited = True
            break
        try:
            with os.scandir(directory) as scanner:
                entries = list(islice(scanner, remaining + 1))
        except OSError:
            limited = True
            continue
        if len(entries) > remaining:
            entries = entries[:remaining]
            limited = True
            pending.clear()
        entries.sort(key=lambda item: item.name)
        for entry in entries:
            entries_seen += 1
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                pending.append(Path(entry.path))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            files_seen += 1
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                limited = True
                continue
            bytes_seen += size
            if bytes_seen > limits.max_total_bytes:
                limited = True
                pending.clear()
                break
            path = Path(entry.path)
            if path.suffix.casefold() not in {
                ".db",
                ".json",
                ".jsonl",
                ".sqlite",
                ".sqlite3",
            }:
                continue
            observations.append(
                FileObservation(
                    path=path,
                    source_ref=path.relative_to(root).as_posix(),
                    size_bytes=size,
                )
            )
    return (
        tuple(sorted(observations, key=lambda item: item.source_ref)),
        files_seen,
        bytes_seen,
        limited,
    )


def _root_fingerprint(
    root: Path,
    observations: tuple[FileObservation, ...],
    checks: tuple[RecoveryCheckResult, ...],
) -> str:
    digest = hashlib.sha256()
    digest.update(root.name.encode())
    for item in observations:
        digest.update(item.source_ref.encode())
        digest.update(str(item.size_bytes).encode())
    for item in checks:
        digest.update(item.source_ref.encode())
        digest.update((item.source_digest or item.evidence_digest).encode())
    return digest.hexdigest()


def _limit_result(files_seen: int, bytes_seen: int) -> RecoveryCheckResult:
    payload = json.dumps(
        {"files_seen": files_seen, "bytes_seen": bytes_seen},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    return RecoveryCheckResult(
        check_id="check-scan-bounds",
        kind="scan_bounds",
        source_ref="data-root",
        status=RecoveryCheckStatus.WARNING,
        reason_code="scan_limit_reached",
        records_checked=files_seen,
        records_omitted=1,
        evidence_digest=digest,
    )
