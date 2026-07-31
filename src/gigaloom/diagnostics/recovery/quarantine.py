"""Content-free quarantine previews for failed authoritative records."""

from __future__ import annotations

import hashlib

from gigaloom.diagnostics.recovery.models import (
    RecoveryActionKind,
    RecoveryActionPreview,
    RecoveryActionStatus,
    RecoveryCheckStatus,
    RecoveryScanReport,
)


_QUARANTINABLE_KINDS = frozenset(
    {
        "attachment_consistency",
        "json_parse",
        "jsonl_integrity",
        "terminal_event_consistency",
    }
)


def preview_quarantines(
    scan: RecoveryScanReport,
) -> tuple[RecoveryActionPreview, ...]:
    """Describe corrupt-record isolation without moving or deleting anything."""
    actions: list[RecoveryActionPreview] = []
    for check in scan.checks:
        if (
            check.kind not in _QUARANTINABLE_KINDS
            or check.status is not RecoveryCheckStatus.FAILED
        ):
            continue
        identity = f"quarantine\0{check.kind}\0{check.source_ref}"
        action_digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        actions.append(
            RecoveryActionPreview(
                action_id=f"action-{action_digest}",
                kind=RecoveryActionKind.QUARANTINE_RECORD,
                target_ref=check.source_ref,
                status=RecoveryActionStatus.RECOMMENDED,
                reason_code=check.reason_code,
                source_digest=check.source_digest,
                expected_digest=None,
                candidate_records=max(check.records_checked, 1),
            )
        )
    return tuple(sorted(actions, key=lambda item: item.action_id))
