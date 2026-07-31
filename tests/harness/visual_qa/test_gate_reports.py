"""Reusable redacted Visual QA gate receipt tests."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

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
    FilesystemVisualArtifactStore,
    FilesystemVisualGateStore,
    ScreenshotRedactionSpec,
    VisualProcessNetworkGrant,
    VisualRedactionPolicy,
    admit_visual_target,
    project_visual_gate_evidence,
    run_visual_gate,
)
from gigaloom.contracts import (
    OperationalEvidenceStatus,
    VisualGateStatus,
    VisualTolerancePolicyV1,
)


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


def _assertions() -> tuple[DomAssertionSpec, ...]:
    return (
        DomAssertionSpec(
            assertion_id="main-visible",
            selector="main",
            kind=DomAssertionKind.VISIBLE,
        ),
    )


def _redaction() -> VisualRedactionPolicy:
    return VisualRedactionPolicy(
        selectors=(
            ScreenshotRedactionSpec(
                redaction_id="auth-card",
                selector="[data-secret]",
            ),
        ),
    )


def _tolerance(
    *,
    max_attempts: int = 1,
    required_passes: int = 1,
    variance_ms: int = 50,
) -> VisualTolerancePolicyV1:
    return VisualTolerancePolicyV1(
        policy_digest=_digest(
            f"tolerance:{max_attempts}:{required_passes}:{variance_ms}"
        ),
        max_console_errors=0,
        max_request_failures=0,
        max_overflow_pixels=0,
        max_timing_variance_ms=variance_ms,
        max_attempts=max_attempts,
        required_passes=required_passes,
    )


class FakeBrowser:
    def __init__(
        self,
        *,
        timings: tuple[int, ...] = (125,),
        scan_passed: bool = True,
        redirects: tuple[str, ...] = (),
    ) -> None:
        self.identity = _browser_identity()
        self._timings = timings
        self._scan_passed = scan_passed
        self._redirects = redirects
        self._calls = 0

    def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
        attempt = self._calls // 2
        self._calls += 1
        return BrowserCaptureResult(
            viewport_id=request.viewport.viewport_id,
            final_url=(
                self._redirects[-1] if self._redirects else request.admission.target_url
            ),
            redirects=self._redirects,
            browser_fingerprint=request.admission.browser.digest,
            screenshot_png=b"\x89PNG\r\n\x1a\nsecret-looking-fixture",
            console=(
                BrowserConsoleObservation(
                    level=BrowserConsoleLevel.INFO,
                    message_digest=_digest("ready"),
                ),
            ),
            requests=(
                BrowserRequestObservation(
                    origin=request.admission.grant.origin,
                    url_digest=_digest("asset"),
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
            timing_ms=self._timings[attempt],
            masked_redaction_ids=("auth-card",),
            screenshot_secret_scan_passed=self._scan_passed,
        )


def _run(tmp_path: Path, browser: FakeBrowser, tolerance=None):
    return run_visual_gate(
        browser=browser,
        admission=_admission(),
        artifact_store=FilesystemVisualArtifactStore(tmp_path),
        receipt_store=FilesystemVisualGateStore(tmp_path),
        tolerance_policy=tolerance or _tolerance(),
        assertions=_assertions(),
        redaction_policy=_redaction(),
    )


def test_passed_gate_persists_two_redacted_artifacts_and_replays(tmp_path: Path):
    receipt = _run(tmp_path, FakeBrowser())

    assert receipt.status is VisualGateStatus.PASSED
    assert receipt.redaction_receipt.status is OperationalEvidenceStatus.PASSED
    assert {item.viewport_id for item in receipt.screenshot_artifacts} == {
        "desktop",
        "mobile",
    }
    assert all(item.redacted for item in receipt.screenshot_artifacts)
    assert FilesystemVisualGateStore(tmp_path).load(receipt.gate_id) == receipt
    assert _run(tmp_path, FakeBrowser()) == receipt

    eval_evidence = project_visual_gate_evidence(receipt, consumer="eval")
    arena_evidence = project_visual_gate_evidence(receipt, consumer="arena")
    assert eval_evidence.status is OperationalEvidenceStatus.PASSED
    assert eval_evidence.evidence_digest == arena_evidence.evidence_digest


def test_failed_redaction_stores_only_placeholder_and_failed_receipt(tmp_path: Path):
    receipt = _run(tmp_path, FakeBrowser(scan_passed=False))

    assert receipt.status is VisualGateStatus.FAILED
    assert receipt.redaction_receipt.status is OperationalEvidenceStatus.FAILED
    for reference in receipt.screenshot_artifacts:
        payload = (tmp_path / reference.relative_path).read_bytes()
        assert b"secret-looking-fixture" not in payload
        assert len(payload) == reference.byte_count


def test_flaky_timing_is_inconclusive_and_cannot_silently_pass(tmp_path: Path):
    receipt = _run(
        tmp_path,
        FakeBrowser(timings=(100, 900)),
        _tolerance(max_attempts=2, required_passes=2, variance_ms=50),
    )

    assert receipt.status is VisualGateStatus.INCONCLUSIVE
    assert any(
        item.kind == "visual.timing_variance"
        and item.status is OperationalEvidenceStatus.FAILED
        for item in receipt.assertions
    )


def test_remote_redirect_is_blocked_before_artifact_persistence(tmp_path: Path):
    with pytest.raises(ValueError, match="loopback-only"):
        _run(
            tmp_path,
            FakeBrowser(redirects=("https://attacker.example/collect",)),
        )
    assert not (tmp_path / "visual").exists()


def test_artifact_store_rejects_count_drift_and_immutable_overwrite(tmp_path: Path):
    admission = _admission()
    request = BrowserCaptureRequest(
        admission=admission,
        viewport=admission.viewports[0],
        assertions=_assertions(),
        redactions=_redaction().selectors,
    )
    capture = FakeBrowser().capture(request)
    store = FilesystemVisualArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="two distinct viewports"):
        store.save_screenshots(
            gate_id="visual-gate-test",
            captures=(capture, capture),
            redaction_policy=_redaction(),
        )

    second = replace(capture, viewport_id="mobile")
    store.save_screenshots(
        gate_id="visual-gate-test",
        captures=(capture, second),
        redaction_policy=_redaction(),
    )
    with pytest.raises(ValueError, match="immutable"):
        store.save_screenshots(
            gate_id="visual-gate-test",
            captures=(
                replace(capture, screenshot_png=b"\x89PNG\r\n\x1a\nchanged"),
                second,
            ),
            redaction_policy=_redaction(),
        )
