"""Content-free RecoveryReceiptV1 assembly and emission."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from gigaloom.contracts import (
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
    RecoveryReceiptPort,
    RecoveryReceiptV1,
)
from gigaloom.diagnostics.fault_lab.contracts import (
    FaultInvariant,
    FaultScenarioResult,
)
from gigaloom.diagnostics.recovery.models import (
    RecoveryActionPreview,
    RecoveryActionStatus,
    RecoveryCheckResult,
    RecoveryCheckStatus,
    RecoveryPreviewReport,
)
from gigaloom.diagnostics.recovery.service import RecoveryCheckService


_CHECK_STATUS = {
    RecoveryCheckStatus.PASSED: OperationalEvidenceStatus.PASSED,
    RecoveryCheckStatus.FAILED: OperationalEvidenceStatus.FAILED,
    RecoveryCheckStatus.WARNING: OperationalEvidenceStatus.WARNING,
    RecoveryCheckStatus.SKIPPED: OperationalEvidenceStatus.SKIPPED,
}
_ACTION_STATUS = {
    RecoveryActionStatus.NOT_NEEDED: OperationalEvidenceStatus.PASSED,
    RecoveryActionStatus.RECOMMENDED: OperationalEvidenceStatus.WARNING,
    RecoveryActionStatus.BLOCKED: OperationalEvidenceStatus.FAILED,
}
_MAX_RECEIPT_EVIDENCE = 256


class RecoveryReceiptService:
    """Run read-only validation and emit one immutable content-free receipt."""

    def __init__(
        self,
        *,
        checks: RecoveryCheckService | None = None,
        clock: Callable[[], datetime] | None = None,
        repository: RecoveryReceiptPort | None = None,
    ) -> None:
        self.checks = checks or RecoveryCheckService()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.repository = repository

    def emit(
        self,
        data_root: str | Path,
        *,
        fault_results: Iterable[FaultScenarioResult] = (),
        omissions: Iterable[str] = (),
    ) -> RecoveryReceiptV1:
        """Create and optionally persist a receipt without storing source content."""
        started_at = _aware_time(self._clock(), "receipt start")
        preview = self.checks.preview(data_root)
        finished_at = _aware_time(self._clock(), "receipt finish")
        receipt = build_recovery_receipt(
            preview,
            fault_results=tuple(fault_results),
            started_at=started_at,
            finished_at=finished_at,
            omissions=tuple(omissions),
        )
        if self.repository is not None:
            self.repository.save(receipt)
        return receipt


def build_recovery_receipt(
    preview: RecoveryPreviewReport,
    *,
    fault_results: tuple[FaultScenarioResult, ...],
    started_at: datetime,
    finished_at: datetime,
    omissions: tuple[str, ...] = (),
) -> RecoveryReceiptV1:
    """Build the frozen F0 receipt contract from bounded A2 evidence."""
    started = _aware_time(started_at, "receipt start")
    finished = _aware_time(finished_at, "receipt finish")
    if finished < started:
        raise ValueError("receipt finish precedes start")
    ordered_faults = tuple(
        sorted(fault_results, key=lambda item: item.fixture_id.value)
    )
    if len({item.fixture_id for item in ordered_faults}) != len(ordered_faults):
        raise ValueError("fault results must have unique fixture ids")
    checks_all = tuple(_check_evidence(item) for item in preview.scan.checks)
    rebuilds_all = tuple(_action_evidence(item) for item in preview.rebuilds)
    quarantines_all = tuple(_action_evidence(item) for item in preview.quarantines)
    invariants_all = tuple(
        _fault_evidence(result, invariant)
        for result in ordered_faults
        for invariant in result.invariants
    )
    checks = checks_all[:_MAX_RECEIPT_EVIDENCE]
    rebuilds = rebuilds_all[:_MAX_RECEIPT_EVIDENCE]
    quarantines = quarantines_all[:_MAX_RECEIPT_EVIDENCE]
    invariants = invariants_all[:_MAX_RECEIPT_EVIDENCE]
    omission_set = set(omissions)
    for field_name, complete, bounded in (
        ("checks", checks_all, checks),
        ("derived_rebuilds", rebuilds_all, rebuilds),
        ("quarantine_previews", quarantines_all, quarantines),
        ("invariants", invariants_all, invariants),
    ):
        if len(complete) != len(bounded):
            omission_set.add(f"{field_name}_truncated")
    normalized_omissions = tuple(sorted(omission_set))
    if not ordered_faults:
        normalized_omissions = tuple(
            sorted({*normalized_omissions, "fault_lab_not_run"})
        )
    receipt_identity = {
        "data_root_fingerprint": preview.scan.data_root_fingerprint,
        "check_catalog_digest": preview.scan.check_catalog_digest,
        "checks": [item.evidence_digest for item in checks],
        "rebuilds": [item.evidence_digest for item in rebuilds],
        "quarantines": [item.evidence_digest for item in quarantines],
        "faults": [item.result_digest for item in ordered_faults],
        "omissions": normalized_omissions,
    }
    receipt_digest = _digest(receipt_identity)
    return RecoveryReceiptV1(
        receipt_id=f"recovery-{receipt_digest[:24]}",
        data_root_fingerprint=preview.scan.data_root_fingerprint,
        check_catalog_digest=preview.scan.check_catalog_digest,
        started_at=started,
        finished_at=finished,
        checks=checks,
        derived_rebuilds=rebuilds,
        quarantine_previews=quarantines,
        fault_fixture_ids=tuple(item.fixture_id.value for item in ordered_faults),
        invariants=invariants,
        omissions=normalized_omissions,
    )


def _check_evidence(check: RecoveryCheckResult) -> OperationalEvidenceV1:
    return OperationalEvidenceV1(
        evidence_id=check.check_id,
        kind=check.kind,
        status=_CHECK_STATUS[check.status],
        evidence_digest=check.evidence_digest,
        reason_code=check.reason_code,
        source_digest=check.source_digest,
    )


def _action_evidence(action: RecoveryActionPreview) -> OperationalEvidenceV1:
    evidence_digest = _digest(
        {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            "reason": action.reason_code,
            "source_digest": action.source_digest,
            "expected_digest": action.expected_digest,
            "candidate_records": action.candidate_records,
            "backup_required": action.backup_required,
            "explicit_command_required": action.explicit_command_required,
        }
    )
    return OperationalEvidenceV1(
        evidence_id=action.action_id,
        kind=action.kind.value,
        status=_ACTION_STATUS[action.status],
        evidence_digest=evidence_digest,
        reason_code=action.reason_code,
        source_digest=action.source_digest,
    )


def _fault_evidence(
    result: FaultScenarioResult,
    invariant: FaultInvariant,
) -> OperationalEvidenceV1:
    evidence_id = hashlib.sha256(
        f"{result.fixture_id.value}\0{invariant.invariant_id}".encode()
    ).hexdigest()[:24]
    return OperationalEvidenceV1(
        evidence_id=f"invariant-{evidence_id}",
        kind="fault_invariant",
        status=(
            OperationalEvidenceStatus.PASSED
            if invariant.passed
            else OperationalEvidenceStatus.FAILED
        ),
        evidence_digest=invariant.evidence_digest,
        reason_code=invariant.reason_code,
        source_digest=result.result_digest,
    )


def _aware_time(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
