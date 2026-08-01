"""Content-free public projections for recovery checks and fault fixtures."""

from __future__ import annotations

from collections import Counter
from typing import Any

from gigaloom.diagnostics.fault_lab.contracts import FaultScenarioResult
from gigaloom.diagnostics.recovery.models import (
    RecoveryCheckStatus,
    RecoveryScanReport,
)


RECOVERY_PROJECTION_SCHEMA_VERSION = 1
MAX_PROJECTED_RECOVERY_CHECKS = 256


def recovery_scan_report_to_dict(report: RecoveryScanReport) -> dict[str, Any]:
    """Project one bounded scan without exposing its absolute data root."""
    counts = Counter(item.status.value for item in report.checks)
    checks = report.checks[:MAX_PROJECTED_RECOVERY_CHECKS]
    return {
        "schema_version": RECOVERY_PROJECTION_SCHEMA_VERSION,
        "kind": "gigaloom_reliability_check",
        "status": _scan_status(report),
        "content_free": True,
        "data_root_fingerprint": report.data_root_fingerprint,
        "check_catalog_digest": report.check_catalog_digest,
        "summary": {
            status.value: counts[status.value] for status in RecoveryCheckStatus
        },
        "bounds": {
            "files_observed": report.files_observed,
            "bytes_observed": report.bytes_observed,
            "checks_observed": len(report.checks),
            "checks_returned": len(checks),
            "checks_truncated": len(checks) != len(report.checks),
        },
        "checks": [
            {
                "check_id": item.check_id,
                "kind": item.kind,
                "source_ref": item.source_ref,
                "status": item.status.value,
                "reason_code": item.reason_code,
                "records_checked": item.records_checked,
                "records_omitted": item.records_omitted,
                "evidence_digest": item.evidence_digest,
                "source_digest": item.source_digest,
            }
            for item in checks
        ],
    }


def fault_scenario_result_to_dict(result: FaultScenarioResult) -> dict[str, Any]:
    """Project one hermetic fault result without sandbox or record content."""
    return {
        "schema_version": RECOVERY_PROJECTION_SCHEMA_VERSION,
        "kind": "gigaloom_reliability_simulation",
        "fixture_id": result.fixture_id.value,
        "status": result.status.value,
        "content_free": True,
        "result_digest": result.result_digest,
        "invariants": [
            {
                "invariant_id": item.invariant_id,
                "passed": item.passed,
                "reason_code": item.reason_code,
                "evidence_digest": item.evidence_digest,
            }
            for item in result.invariants
        ],
    }


def _scan_status(report: RecoveryScanReport) -> str:
    if report.failed:
        return RecoveryCheckStatus.FAILED.value
    if any(item.status is RecoveryCheckStatus.WARNING for item in report.checks):
        return RecoveryCheckStatus.WARNING.value
    return RecoveryCheckStatus.PASSED.value


__all__ = [
    "MAX_PROJECTED_RECOVERY_CHECKS",
    "RECOVERY_PROJECTION_SCHEMA_VERSION",
    "fault_scenario_result_to_dict",
    "recovery_scan_report_to_dict",
]
