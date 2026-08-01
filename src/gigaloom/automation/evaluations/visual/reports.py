"""Reusable Visual QA gate receipt construction and persistence."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

from gigaloom.automation.evaluations.visual.artifacts import (
    VisualArtifactStorePort,
    screenshot_is_redacted,
)
from gigaloom.automation.evaluations.visual.assertions import DomAssertionSpec
from gigaloom.automation.evaluations.visual.browser import VisualBrowserPort
from gigaloom.automation.evaluations.visual.contracts import VisualBrowserAdmission
from gigaloom.automation.evaluations.visual.evidence import (
    CollectedVisualEvidence,
    collect_browser_evidence,
)
from gigaloom.automation.evaluations.visual.redaction import VisualRedactionPolicy
from gigaloom.contracts import (
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
    VisualEvidenceSummaryV1,
    VisualGateReceiptV1,
    VisualGateStatus,
    VisualTolerancePolicyV1,
    visual_gate_receipt_from_dict,
    visual_gate_receipt_to_dict,
)
from gigaloom.contracts.operational_validation import canonical_digest


@runtime_checkable
class VisualGateStorePort(Protocol):
    """Durable boundary for immutable reusable gate receipts."""

    def save(self, receipt: VisualGateReceiptV1) -> None:
        """Persist one immutable receipt."""


class FilesystemVisualGateStore:
    """Atomic immutable JSON receipt storage below one root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save(self, receipt: VisualGateReceiptV1) -> None:
        """Persist one strict canonical receipt without overwriting drift."""
        payload = visual_gate_receipt_to_dict(receipt)
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        destination = self._root / "receipts" / f"{receipt.gate_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != encoded:
                raise ValueError("visual gate receipt is immutable")
            return
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(encoded)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, gate_id: str) -> VisualGateReceiptV1:
        """Load and strictly verify one saved receipt."""
        payload = json.loads(
            (self._root / "receipts" / f"{gate_id}.json").read_text("utf-8")
        )
        return visual_gate_receipt_from_dict(payload)


def run_visual_gate(
    *,
    browser: VisualBrowserPort,
    admission: VisualBrowserAdmission,
    artifact_store: VisualArtifactStorePort,
    receipt_store: VisualGateStorePort,
    tolerance_policy: VisualTolerancePolicyV1,
    assertions: tuple[DomAssertionSpec, ...] = (),
    redaction_policy: VisualRedactionPolicy | None = None,
    revalidate_admission: Callable[[], None] | None = None,
) -> VisualGateReceiptV1:
    """Run every declared attempt and emit one reusable fail-closed receipt."""
    redaction = redaction_policy or VisualRedactionPolicy()
    attempts = tuple(
        collect_browser_evidence(
            browser=browser,
            admission=admission,
            assertions=assertions,
            redaction_policy=redaction,
        )
        for _ in range(tolerance_policy.max_attempts)
    )
    redaction_passed = all(
        screenshot_is_redacted(capture, redaction)
        for attempt in attempts
        for capture in attempt.captures
    )
    timing_evidence = _timing_variance_evidence(attempts, tolerance_policy)
    attempt_passes = tuple(
        _attempt_passed(item, tolerance_policy, redaction) for item in attempts
    )
    timing_stable = all(
        item.status is OperationalEvidenceStatus.PASSED for item in timing_evidence
    )
    summaries_within_tolerance = all(
        item.console_summary.failure_count <= tolerance_policy.max_console_errors
        and item.request_summary.failure_count <= tolerance_policy.max_request_failures
        for item in attempts
    )
    if not timing_stable:
        status = VisualGateStatus.INCONCLUSIVE
    elif (
        redaction_passed
        and summaries_within_tolerance
        and sum(attempt_passes) >= tolerance_policy.required_passes
    ):
        status = VisualGateStatus.PASSED
    else:
        status = VisualGateStatus.FAILED
    selected_index = max(
        (index for index, passed in enumerate(attempt_passes) if passed),
        default=len(attempts) - 1,
    )
    selected = attempts[selected_index]
    if revalidate_admission is not None:
        revalidate_admission()
    gate_digest = canonical_digest(
        {
            "admission": admission.origin_policy_digest,
            "assertions": [item.digest for item in assertions],
            "attempts": [_attempt_digest(item) for item in attempts],
            "redaction": redaction.digest,
            "status": status.value,
            "tolerance": tolerance_policy.policy_digest,
        }
    )
    gate_id = f"visual-gate-{gate_digest[:24]}"
    artifacts = artifact_store.save_screenshots(
        gate_id=gate_id,
        captures=selected.captures,
        redaction_policy=redaction,
    )
    assertions_for_receipt = (
        tuple(
            item
            for item in selected.assertions
            if item.kind != "visual.horizontal_overflow"
        )
        + _overflow_evidence(selected, tolerance_policy)
        + timing_evidence
    )
    redaction_receipt = OperationalEvidenceV1(
        evidence_id="visual-redaction",
        kind="visual.redaction",
        status=(
            OperationalEvidenceStatus.PASSED
            if redaction_passed
            else OperationalEvidenceStatus.FAILED
        ),
        evidence_digest=canonical_digest(
            {
                "attempts": [
                    [
                        {
                            "masked_ids": list(capture.masked_redaction_ids),
                            "scan_passed": capture.screenshot_secret_scan_passed,
                            "viewport_id": capture.viewport_id,
                        }
                        for capture in attempt.captures
                    ]
                    for attempt in attempts
                ],
                "policy_digest": redaction.digest,
            }
        ),
        reason_code=(
            "redaction_verified" if redaction_passed else "redaction_failed_closed"
        ),
        source_digest=redaction.digest,
    )
    receipt = VisualGateReceiptV1(
        gate_id=gate_id,
        origin=admission.grant.origin,
        origin_policy_digest=admission.origin_policy_digest,
        source_revision=admission.grant.source_revision,
        browser_fingerprint=admission.browser.digest,
        viewports=admission.viewports,
        assertions=assertions_for_receipt,
        console_summary=_worst_summary(attempts, "console_summary"),
        request_summary=_worst_summary(attempts, "request_summary"),
        screenshot_artifacts=artifacts,
        redaction_receipt=redaction_receipt,
        tolerance_policy=tolerance_policy,
        status=status,
    )
    receipt_store.save(receipt)
    return receipt


def project_visual_gate_evidence(
    receipt: VisualGateReceiptV1,
    *,
    consumer: str,
) -> OperationalEvidenceV1:
    """Project a receipt for Eval or Arena without importing implementation."""
    if consumer not in {"eval", "arena"}:
        raise ValueError("visual gate consumer must be eval or arena")
    payload = visual_gate_receipt_to_dict(receipt)
    receipt_digest = canonical_digest(payload)
    return OperationalEvidenceV1(
        evidence_id=f"visual-gate.{consumer}.{receipt.gate_id}",
        kind=f"visual.gate.{consumer}",
        status={
            VisualGateStatus.PASSED: OperationalEvidenceStatus.PASSED,
            VisualGateStatus.FAILED: OperationalEvidenceStatus.FAILED,
            VisualGateStatus.INCONCLUSIVE: OperationalEvidenceStatus.UNKNOWN,
        }[receipt.status],
        evidence_digest=receipt_digest,
        reason_code=f"visual_gate_{receipt.status.value}",
        source_digest=receipt.origin_policy_digest,
    )


def _attempt_passed(
    attempt: CollectedVisualEvidence,
    tolerance: VisualTolerancePolicyV1,
    redaction: VisualRedactionPolicy,
) -> bool:
    return bool(
        all(
            item.status is not OperationalEvidenceStatus.FAILED
            for item in attempt.assertions
            if item.kind != "visual.horizontal_overflow"
        )
        and max(capture.overflow_pixels for capture in attempt.captures)
        <= tolerance.max_overflow_pixels
        and attempt.console_summary.failure_count <= tolerance.max_console_errors
        and attempt.request_summary.failure_count <= tolerance.max_request_failures
        and all(
            screenshot_is_redacted(capture, redaction) for capture in attempt.captures
        )
    )


def _overflow_evidence(
    attempt: CollectedVisualEvidence,
    tolerance: VisualTolerancePolicyV1,
) -> tuple[OperationalEvidenceV1, ...]:
    return tuple(
        OperationalEvidenceV1(
            evidence_id=f"overflow.{capture.viewport_id}",
            kind="visual.horizontal_overflow",
            status=(
                OperationalEvidenceStatus.PASSED
                if capture.overflow_pixels <= tolerance.max_overflow_pixels
                else OperationalEvidenceStatus.FAILED
            ),
            evidence_digest=canonical_digest(
                {
                    "limit": tolerance.max_overflow_pixels,
                    "observed": capture.overflow_pixels,
                    "viewport_id": capture.viewport_id,
                }
            ),
            reason_code=(
                "overflow_within_tolerance"
                if capture.overflow_pixels <= tolerance.max_overflow_pixels
                else "overflow_exceeded"
            ),
            source_digest=tolerance.policy_digest,
        )
        for capture in attempt.captures
    )


def _timing_variance_evidence(
    attempts: tuple[CollectedVisualEvidence, ...],
    tolerance: VisualTolerancePolicyV1,
) -> tuple[OperationalEvidenceV1, ...]:
    results = []
    for viewport_id in sorted(dict(attempts[0].timing_ms)):
        values = [dict(item.timing_ms)[viewport_id] for item in attempts]
        variance = max(values) - min(values)
        passed = variance <= tolerance.max_timing_variance_ms
        results.append(
            OperationalEvidenceV1(
                evidence_id=f"timing-variance.{viewport_id}",
                kind="visual.timing_variance",
                status=(
                    OperationalEvidenceStatus.PASSED
                    if passed
                    else OperationalEvidenceStatus.FAILED
                ),
                evidence_digest=canonical_digest(
                    {
                        "limit_ms": tolerance.max_timing_variance_ms,
                        "values_ms": values,
                        "variance_ms": variance,
                        "viewport_id": viewport_id,
                    }
                ),
                reason_code=("timing_stable" if passed else "timing_flaky"),
                source_digest=tolerance.policy_digest,
            )
        )
    return tuple(results)


def _worst_summary(
    attempts: tuple[CollectedVisualEvidence, ...],
    field_name: str,
) -> VisualEvidenceSummaryV1:
    summaries = tuple(getattr(item, field_name) for item in attempts)
    return VisualEvidenceSummaryV1(
        total_count=sum(item.total_count for item in summaries),
        failure_count=max(item.failure_count for item in summaries),
        evidence_digest=canonical_digest(
            [
                {
                    "digest": item.evidence_digest,
                    "failure_count": item.failure_count,
                    "total_count": item.total_count,
                }
                for item in summaries
            ]
        ),
    )


def _attempt_digest(attempt: CollectedVisualEvidence) -> str:
    return canonical_digest(
        {
            "assertions": [item.evidence_digest for item in attempt.assertions],
            "console": attempt.console_summary.evidence_digest,
            "requests": attempt.request_summary.evidence_digest,
            "screenshots": [
                canonical_digest(capture.screenshot_png.hex())
                for capture in attempt.captures
            ],
            "timing_ms": list(attempt.timing_ms),
        }
    )


__all__ = [
    "FilesystemVisualGateStore",
    "VisualGateStorePort",
    "project_visual_gate_evidence",
    "run_visual_gate",
]
