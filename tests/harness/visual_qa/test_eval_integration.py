"""Visual QA Eval operator and CLI integration tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gigaloom.automation.evaluations.visual.api import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    BrowserConsoleLevel,
    BrowserConsoleObservation,
    BrowserFingerprint,
    BrowserRequestObservation,
    VisualProcessNetworkGrant,
    run_visual_eval,
)
from gigaloom.cli_commands.main import main as cli_main
from gigaloom.contracts import OperationalEvidenceStatus, VisualGateStatus


PNG = b"\x89PNG\r\n\x1a\nvisual-eval-fixture"
TARGET_URL = "http://127.0.0.1:39001/app"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _browser_identity() -> BrowserFingerprint:
    return BrowserFingerprint(
        engine="chromium",
        browser_version="test-149",
        executable_digest=_digest("browser"),
        automation_name="playwright",
        automation_version="1.61.0",
    )


class FakeInspector:
    def __init__(self, *, drift: bool = False) -> None:
        self.grant = VisualProcessNetworkGrant(
            grant_id="visual-grant-test",
            process_id=42,
            origin="http://127.0.0.1:39001",
            source_revision="git-deadbeef-clean",
            network_policy_digest=_digest("exact-origin:42"),
        )
        self.drift = drift
        self.listener_revalidations = 0
        self.full_revalidations = 0

    def resolve(self, target_url: str) -> VisualProcessNetworkGrant:
        assert target_url == TARGET_URL
        return self.grant

    def revalidate(
        self,
        target_url: str,
        expected: VisualProcessNetworkGrant,
    ) -> None:
        assert target_url == TARGET_URL
        assert expected == self.grant
        self.full_revalidations += 1
        if self.drift:
            raise ValueError("Visual QA target changed after admission")

    def revalidate_listener(
        self,
        target_url: str,
        expected: VisualProcessNetworkGrant,
    ) -> None:
        assert target_url == TARGET_URL
        assert expected == self.grant
        self.listener_revalidations += 1


class FakeBrowser:
    def __init__(
        self,
        *,
        console_error: bool = False,
        overflow_pixels: int = 0,
        request_failure: bool = False,
    ) -> None:
        self.identity = _browser_identity()
        self._console_error = console_error
        self._overflow_pixels = overflow_pixels
        self._request_failure = request_failure

    def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
        return BrowserCaptureResult(
            viewport_id=request.viewport.viewport_id,
            final_url=request.admission.target_url,
            redirects=(),
            browser_fingerprint=self.identity.digest,
            screenshot_png=PNG,
            console=(
                BrowserConsoleObservation(
                    level=(
                        BrowserConsoleLevel.ERROR
                        if self._console_error
                        else BrowserConsoleLevel.INFO
                    ),
                    message_digest=_digest("console"),
                ),
            ),
            requests=(
                BrowserRequestObservation(
                    origin=request.admission.grant.origin,
                    url_digest=_digest("request"),
                    method="GET",
                    status_code=500 if self._request_failure else 200,
                ),
            ),
            dom=(),
            client_width=request.viewport.width,
            scroll_width=request.viewport.width + self._overflow_pixels,
            timing_ms=100,
            screenshot_secret_scan_passed=True,
        )


def _run(
    tmp_path: Path,
    *,
    browser: FakeBrowser | None = None,
    assertions: tuple[str, ...] = (
        "no-console-errors",
        "no-horizontal-overflow",
    ),
    inspector: FakeInspector | None = None,
):
    effective_browser = browser or FakeBrowser()
    effective_inspector = inspector or FakeInspector()
    result = run_visual_eval(
        target_url=TARGET_URL,
        assertions=assertions,
        evidence_root=tmp_path,
        browser_factory=lambda: effective_browser,
        inspector=effective_inspector,
    )
    return result, effective_inspector


def test_visual_eval_is_reusable_deterministic_and_content_free(tmp_path: Path):
    first, inspector = _run(tmp_path)
    second, _ = _run(tmp_path)

    assert first == second
    assert first.receipt.status is VisualGateStatus.PASSED
    assert first.eval_evidence.status is OperationalEvidenceStatus.PASSED
    assert inspector.listener_revalidations == 4
    assert inspector.full_revalidations == 1
    assert [item.viewport_id for item in first.receipt.viewports] == [
        "desktop",
        "mobile",
    ]
    assert all(
        (tmp_path / item.relative_path).read_bytes() == PNG
        for item in first.receipt.screenshot_artifacts
    )
    encoded = (tmp_path / "receipts" / f"{first.receipt.gate_id}.json").read_text()
    assert TARGET_URL not in encoded
    assert "visual-eval-fixture" not in encoded


def test_selected_assertions_and_request_failures_drive_gate_status(tmp_path: Path):
    failed, _ = _run(
        tmp_path / "failed",
        browser=FakeBrowser(console_error=True, overflow_pixels=12),
    )
    assert failed.receipt.status is VisualGateStatus.FAILED
    assert failed.receipt.console_summary.failure_count == 2
    assert {
        item.evidence_id
        for item in failed.receipt.assertions
        if item.status is OperationalEvidenceStatus.FAILED
    } == {"overflow.desktop", "overflow.mobile"}

    tolerated, _ = _run(
        tmp_path / "tolerated",
        browser=FakeBrowser(overflow_pixels=12),
        assertions=("no-console-errors",),
    )
    assert tolerated.receipt.status is VisualGateStatus.PASSED

    request_failed, _ = _run(
        tmp_path / "request-failed",
        browser=FakeBrowser(request_failure=True),
        assertions=("no-horizontal-overflow",),
    )
    assert request_failed.receipt.status is VisualGateStatus.FAILED


def test_duplicate_or_missing_assertions_fail_before_browser_creation(tmp_path: Path):
    called = False

    def factory():
        nonlocal called
        called = True
        return FakeBrowser()

    for assertions in ((), ("no-console-errors", "no-console-errors")):
        with pytest.raises(ValueError, match="assert"):
            run_visual_eval(
                target_url=TARGET_URL,
                assertions=assertions,
                evidence_root=tmp_path,
                browser_factory=factory,
                inspector=FakeInspector(),
            )
    assert called is False


def test_target_drift_blocks_artifact_and_receipt_persistence(tmp_path: Path):
    with pytest.raises(ValueError, match="changed after admission"):
        _run(tmp_path, inspector=FakeInspector(drift=True))

    assert not (tmp_path / "receipts").exists()
    assert not (tmp_path / "visual").exists()


def test_exact_public_cli_is_state_free_and_returns_eval_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    result, _ = _run(tmp_path / "prepared")
    import gigaloom.cli_commands.handlers.visual_eval as handler
    import gigaloom.cli_commands.main as cli_module

    observed: dict[str, object] = {}

    def fake_run_visual_eval(**kwargs):
        observed.update(kwargs)
        return result

    monkeypatch.setattr(handler, "run_visual_eval", fake_run_visual_eval)
    monkeypatch.setattr(
        cli_module,
        "prepare_runtime_state",
        lambda: pytest.fail("state-free Visual QA must not prepare runtime state"),
    )
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(tmp_path / "state"))

    exit_code = cli_main(
        [
            "eval",
            "visual",
            "--url",
            TARGET_URL,
            "--assert",
            "no-console-errors",
            "--assert",
            "no-horizontal-overflow",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert observed == {
        "target_url": TARGET_URL,
        "assertions": ("no-console-errors", "no-horizontal-overflow"),
        "evidence_root": tmp_path / "state" / "automation" / "visual-qa-v1",
    }
    assert payload["visual_gate"]["status"] == "passed"
    assert payload["eval_evidence"]["kind"] == "visual.gate.eval"
    assert payload["assertions"] == [
        "no-console-errors",
        "no-horizontal-overflow",
    ]


def test_cli_returns_gate_failure_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    result, _ = _run(
        tmp_path / "prepared",
        browser=FakeBrowser(console_error=True),
    )
    import gigaloom.cli_commands.handlers.visual_eval as handler

    monkeypatch.setattr(handler, "run_visual_eval", lambda **_: result)
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(tmp_path / "state"))

    exit_code = cli_main(
        [
            "eval",
            "visual",
            "--url",
            TARGET_URL,
            "--assert",
            "no-console-errors",
            "--json",
        ]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["visual_gate"]["status"] == "failed"
