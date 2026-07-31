"""Evidence collection tests for the bounded Visual QA browser port."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from gigaloom.automation.evaluations.visual.api import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    BrowserConsoleLevel,
    BrowserConsoleObservation,
    BrowserDomObservation,
    BrowserFingerprint,
    BrowserRequestObservation,
    DomAssertionKind,
    DomAssertionSpec,
    VisualProcessNetworkGrant,
    admit_visual_target,
    collect_browser_evidence,
)
from gigaloom.contracts import OperationalEvidenceStatus


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _browser_identity() -> BrowserFingerprint:
    return BrowserFingerprint(
        engine="chromium",
        browser_version="140.0.7339.16",
        executable_digest=_digest("chromium"),
        automation_name="playwright",
        automation_version="1.61.0",
    )


def _admission():
    return admit_visual_target(
        target_url="http://127.0.0.1:3000/app",
        grant=VisualProcessNetworkGrant(
            grant_id="visual-grant-1",
            process_id=42,
            origin="http://127.0.0.1:3000",
            source_revision="deadbeef",
            network_policy_digest=_digest("deny-external"),
        ),
        browser=_browser_identity(),
    )


def _capture(request: BrowserCaptureRequest) -> BrowserCaptureResult:
    return BrowserCaptureResult(
        viewport_id=request.viewport.viewport_id,
        final_url="http://127.0.0.1:3000/app/ready",
        redirects=("http://127.0.0.1:3000/loading",),
        browser_fingerprint=request.admission.browser.digest,
        screenshot_png=b"\x89PNG\r\n\x1a\nfixture",
        console=(
            BrowserConsoleObservation(
                level=BrowserConsoleLevel.INFO,
                message_digest=_digest("ready"),
            ),
        ),
        requests=(
            BrowserRequestObservation(
                origin=request.admission.grant.origin,
                url_digest=_digest(f"asset:{request.viewport.viewport_id}"),
                method="GET",
                status_code=200,
            ),
        ),
        dom=(
            BrowserDomObservation(
                assertion_id="main-visible",
                matched_count=1,
                visible_count=1,
            ),
        ),
        client_width=request.viewport.width,
        scroll_width=request.viewport.width,
        timing_ms=125,
    )


class FakeBrowser:
    def __init__(self) -> None:
        self.identity = _browser_identity()

    def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
        return _capture(request)


def _assertions() -> tuple[DomAssertionSpec, ...]:
    return (
        DomAssertionSpec(
            assertion_id="main-visible",
            selector="main",
            kind=DomAssertionKind.VISIBLE,
        ),
    )


def test_collects_two_viewports_console_requests_overflow_dom_and_timing():
    evidence = collect_browser_evidence(
        browser=FakeBrowser(),
        admission=_admission(),
        assertions=_assertions(),
    )

    assert [item.viewport_id for item in evidence.captures] == ["desktop", "mobile"]
    assert evidence.screenshot_byte_count == 30
    assert evidence.console_summary.total_count == 2
    assert evidence.console_summary.failure_count == 0
    assert evidence.request_summary.total_count == 2
    assert evidence.request_summary.failure_count == 0
    assert evidence.timing_ms == (("desktop", 125), ("mobile", 125))
    assert all(
        item.status is OperationalEvidenceStatus.PASSED for item in evidence.assertions
    )
    assert {item.kind for item in evidence.assertions} == {
        "visual.dom.visible",
        "visual.horizontal_overflow",
        "visual.timing",
    }


def test_console_request_overflow_and_missing_dom_are_failure_evidence():
    class FailingBrowser(FakeBrowser):
        def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
            capture = _capture(request)
            return replace(
                capture,
                console=(
                    BrowserConsoleObservation(
                        level=BrowserConsoleLevel.ERROR,
                        message_digest=_digest("boom"),
                    ),
                ),
                requests=(
                    BrowserRequestObservation(
                        origin="https://telemetry.example",
                        url_digest=_digest("external-attempt"),
                        method="POST",
                        failure_code="blocked_by_policy",
                        blocked=True,
                    ),
                ),
                dom=(),
                scroll_width=capture.client_width + 12,
            )

    evidence = collect_browser_evidence(
        browser=FailingBrowser(),
        admission=_admission(),
        assertions=_assertions(),
    )

    assert evidence.console_summary.failure_count == 2
    assert evidence.request_summary.failure_count == 2
    assert {
        item.kind
        for item in evidence.assertions
        if item.status is OperationalEvidenceStatus.FAILED
    } == {"visual.dom.visible", "visual.horizontal_overflow"}


def test_unblocked_external_request_is_an_authority_violation():
    class EscapingBrowser(FakeBrowser):
        def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
            return replace(
                _capture(request),
                requests=(
                    BrowserRequestObservation(
                        origin="https://telemetry.example",
                        url_digest=_digest("external"),
                        method="GET",
                        status_code=200,
                    ),
                ),
            )

    with pytest.raises(ValueError, match="allowed an external request"):
        collect_browser_evidence(
            browser=EscapingBrowser(),
            admission=_admission(),
        )


def test_collection_rejects_browser_or_viewport_identity_drift():
    drifted = replace(_browser_identity(), browser_version="141.0")
    browser = FakeBrowser()
    browser.identity = drifted
    with pytest.raises(ValueError, match="identity differs"):
        collect_browser_evidence(browser=browser, admission=_admission())

    class WrongViewportBrowser(FakeBrowser):
        def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
            return replace(_capture(request), viewport_id="tablet")

    with pytest.raises(ValueError, match="wrong viewport"):
        collect_browser_evidence(
            browser=WrongViewportBrowser(),
            admission=_admission(),
        )


def test_capture_and_request_count_ceiling_fail_closed():
    admission = replace(_admission(), max_requests=1)
    with pytest.raises(ValueError, match="request limit"):
        collect_browser_evidence(browser=FakeBrowser(), admission=admission)

    with pytest.raises(ValueError, match="bounded PNG"):
        replace(
            _capture(BrowserCaptureRequest(_admission(), _admission().viewports[0])),
            screenshot_png=b"not-png",
        )
