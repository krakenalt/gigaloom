"""Bounded collection of browser evidence for the Visual QA gate."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.automation.evaluations.visual.admission import validate_navigation_url
from gigaloom.automation.evaluations.visual.assertions import (
    DomAssertionSpec,
    evaluate_dom_assertion,
)
from gigaloom.automation.evaluations.visual.browser import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    BrowserConsoleLevel,
    VisualBrowserPort,
)
from gigaloom.automation.evaluations.visual.contracts import VisualBrowserAdmission
from gigaloom.contracts import (
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
    VisualEvidenceSummaryV1,
)
from gigaloom.contracts.operational_validation import canonical_digest


MAX_ATTEMPT_SCREENSHOT_BYTES = 40 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CollectedVisualEvidence:
    """One complete two-viewport, content-bounded browser attempt."""

    captures: tuple[BrowserCaptureResult, BrowserCaptureResult]
    assertions: tuple[OperationalEvidenceV1, ...]
    console_summary: VisualEvidenceSummaryV1
    request_summary: VisualEvidenceSummaryV1
    timing_ms: tuple[tuple[str, int], ...]

    @property
    def screenshot_byte_count(self) -> int:
        """Return the total bounded raw screenshot bytes for this attempt."""
        return sum(len(item.screenshot_png) for item in self.captures)


def collect_browser_evidence(
    *,
    browser: VisualBrowserPort,
    admission: VisualBrowserAdmission,
    assertions: tuple[DomAssertionSpec, ...] = (),
) -> CollectedVisualEvidence:
    """Capture both admitted viewports and fail closed on authority drift."""
    if browser.identity.digest != admission.browser.digest:
        raise ValueError("visual browser identity differs from admission")
    captures: list[BrowserCaptureResult] = []
    evidence: list[OperationalEvidenceV1] = []
    for viewport in admission.viewports:
        capture = browser.capture(
            BrowserCaptureRequest(
                admission=admission,
                viewport=viewport,
                assertions=assertions,
            )
        )
        _validate_capture(
            capture,
            admission=admission,
            viewport_id=viewport.viewport_id,
        )
        captures.append(capture)
        evidence.extend(_dom_evidence(capture, assertions))
        evidence.append(_overflow_evidence(capture))
        evidence.append(_timing_evidence(capture))
    if (
        sum(len(item.screenshot_png) for item in captures)
        > MAX_ATTEMPT_SCREENSHOT_BYTES
    ):
        raise ValueError("visual screenshots exceed the attempt byte limit")
    requests = tuple(item for capture in captures for item in capture.requests)
    if len(requests) > admission.max_requests:
        raise ValueError("visual capture exceeded the request limit")
    console = tuple(item for capture in captures for item in capture.console)
    typed_captures = (captures[0], captures[1])
    return CollectedVisualEvidence(
        captures=typed_captures,
        assertions=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        console_summary=VisualEvidenceSummaryV1(
            total_count=len(console),
            failure_count=sum(
                item.level in {BrowserConsoleLevel.ERROR, BrowserConsoleLevel.ASSERT}
                for item in console
            ),
            evidence_digest=canonical_digest(
                [(item.level.value, item.message_digest) for item in console]
            ),
        ),
        request_summary=VisualEvidenceSummaryV1(
            total_count=len(requests),
            failure_count=sum(item.failed for item in requests),
            evidence_digest=canonical_digest(
                [
                    (
                        item.origin,
                        item.url_digest,
                        item.method,
                        item.status_code,
                        item.failure_code,
                        item.blocked,
                    )
                    for item in requests
                ]
            ),
        ),
        timing_ms=tuple(
            (item.viewport_id, item.timing_ms)
            for item in sorted(captures, key=lambda item: item.viewport_id)
        ),
    )


def _validate_capture(
    capture: BrowserCaptureResult,
    *,
    admission: VisualBrowserAdmission,
    viewport_id: str,
) -> None:
    if capture.viewport_id != viewport_id:
        raise ValueError("visual browser returned the wrong viewport")
    if capture.browser_fingerprint != admission.browser.digest:
        raise ValueError("visual capture browser fingerprint drifted")
    validate_navigation_url(
        admission,
        capture.final_url,
        redirects=capture.redirects,
    )
    for request in capture.requests:
        if request.origin != admission.grant.origin and not request.blocked:
            raise ValueError("visual browser allowed an external request")


def _dom_evidence(
    capture: BrowserCaptureResult,
    assertions: tuple[DomAssertionSpec, ...],
) -> tuple[OperationalEvidenceV1, ...]:
    observations = {item.assertion_id: item for item in capture.dom}
    results: list[OperationalEvidenceV1] = []
    for spec in assertions:
        observed = observations.get(spec.assertion_id)
        results.append(
            evaluate_dom_assertion(
                spec,
                viewport_id=capture.viewport_id,
                matched_count=observed.matched_count if observed is not None else 0,
                visible_count=observed.visible_count if observed is not None else 0,
                text_digest=observed.text_digest if observed is not None else None,
            )
        )
    return tuple(results)


def _overflow_evidence(capture: BrowserCaptureResult) -> OperationalEvidenceV1:
    overflow = capture.overflow_pixels
    return OperationalEvidenceV1(
        evidence_id=f"overflow.{capture.viewport_id}",
        kind="visual.horizontal_overflow",
        status=(
            OperationalEvidenceStatus.PASSED
            if overflow == 0
            else OperationalEvidenceStatus.FAILED
        ),
        evidence_digest=canonical_digest(
            {
                "client_width": capture.client_width,
                "overflow_pixels": overflow,
                "scroll_width": capture.scroll_width,
                "viewport_id": capture.viewport_id,
            }
        ),
        reason_code="no_overflow" if overflow == 0 else "horizontal_overflow",
    )


def _timing_evidence(capture: BrowserCaptureResult) -> OperationalEvidenceV1:
    return OperationalEvidenceV1(
        evidence_id=f"timing.{capture.viewport_id}",
        kind="visual.timing",
        status=OperationalEvidenceStatus.PASSED,
        evidence_digest=canonical_digest(
            {
                "timing_ms": capture.timing_ms,
                "viewport_id": capture.viewport_id,
            }
        ),
        reason_code="timing_observed",
    )


__all__ = ["CollectedVisualEvidence", "collect_browser_evidence"]
