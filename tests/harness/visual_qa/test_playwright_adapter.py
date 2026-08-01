"""Concrete Playwright Visual QA adapter boundary tests."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Mapping, cast

import pytest

from gigaloom.automation.evaluations.visual.api import (
    BrowserCaptureRequest,
    BrowserConsoleLevel,
    BrowserFingerprint,
    PlaywrightVisualBrowser,
    VisualProcessNetworkGrant,
    admit_visual_target,
)


PNG = b"\x89PNG\r\n\x1a\nplaywright-adapter-fixture"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity() -> BrowserFingerprint:
    return BrowserFingerprint(
        engine="chromium",
        browser_version="test-149",
        executable_digest=_digest("browser"),
        automation_name="playwright",
        automation_version="1.61.0",
    )


def _request() -> BrowserCaptureRequest:
    identity = _identity()
    admission = admit_visual_target(
        target_url="http://127.0.0.1:39001/app",
        grant=VisualProcessNetworkGrant(
            grant_id="visual-grant-adapter",
            process_id=42,
            origin="http://127.0.0.1:39001",
            source_revision="git-deadbeef-clean",
            network_policy_digest=_digest("network"),
        ),
        browser=identity,
    )
    return BrowserCaptureRequest(admission, admission.viewports[0])


def _capture_payload() -> dict[str, Any]:
    return {
        "console": [
            {
                "level": "warning",
                "messageDigest": _digest("content-never-crosses"),
            }
        ],
        "dom": [],
        "finalUrl": "http://127.0.0.1:39001/app",
        "maskedRedactionIds": [],
        "redirects": [],
        "requests": [
            {
                "blocked": False,
                "failureCode": None,
                "method": "GET",
                "origin": "http://127.0.0.1:39001",
                "statusCode": 200,
                "urlDigest": _digest("http://127.0.0.1:39001/app"),
            }
        ],
        "screenshotBase64": base64.b64encode(PNG).decode("ascii"),
        "screenshotSecretScanPassed": True,
        "timingMs": 125,
        "viewportId": "desktop",
        "widths": {"client": 1440, "scroll": 1440},
    }


def _browser(
    tmp_path: Path,
    invoke,
) -> tuple[PlaywrightVisualBrowser, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "chromium"
    executable.write_bytes(b"fixed-browser")
    stat = executable.stat()
    browser = PlaywrightVisualBrowser(
        identity=_identity(),
        executable_path=executable,
        executable_stat=(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns),
        invoke=invoke,
    )
    return browser, executable


def test_adapter_decodes_only_bounded_content_free_bridge_evidence(tmp_path: Path):
    observed: dict[str, object] = {}

    def invoke(
        mode: str,
        payload: Mapping[str, Any] | None,
        timeout: float,
    ) -> Mapping[str, Any]:
        observed.update({"mode": mode, "payload": payload, "timeout": timeout})
        return _capture_payload()

    browser, _ = _browser(tmp_path, invoke)
    result = browser.capture(_request())

    assert result.screenshot_png == PNG
    assert result.console[0].level is BrowserConsoleLevel.WARNING
    assert result.console[0].message_digest == _digest("content-never-crosses")
    assert result.requests[0].status_code == 200
    assert result.screenshot_secret_scan_passed is True
    assert observed["mode"] == "capture"
    assert observed["timeout"] == 20.0
    raw_payload = observed["payload"]
    assert isinstance(raw_payload, Mapping)
    payload = cast(Mapping[str, object], raw_payload)
    assert payload["maxRequests"] == 64
    assert payload["timeoutMs"] == 15_000
    assert set(payload) == {
        "assertions",
        "deviceScaleFactor",
        "height",
        "maxRedirects",
        "maxRequests",
        "origin",
        "redactions",
        "targetUrl",
        "timeoutMs",
        "viewportId",
        "width",
    }


def test_adapter_rejects_unknown_bridge_fields_and_executable_drift(tmp_path: Path):
    payload = _capture_payload()
    payload["rawConsoleMessage"] = "must never cross"
    browser, executable = _browser(tmp_path, lambda *_: payload)

    with pytest.raises(ValueError, match="invalid schema"):
        browser.capture(_request())

    stable_browser, executable = _browser(tmp_path / "drift", lambda *_: {})
    executable.write_bytes(b"changed-browser")
    with pytest.raises(ValueError, match="changed after admission"):
        stable_browser.capture(_request())


def test_adapter_rejects_invalid_or_oversized_screenshot_encoding(tmp_path: Path):
    invalid = _capture_payload()
    invalid["screenshotBase64"] = "not-base64!"
    browser, _ = _browser(tmp_path / "invalid", lambda *_: invalid)
    with pytest.raises(ValueError, match="invalid screenshot"):
        browser.capture(_request())

    oversized = _capture_payload()
    oversized["screenshotBase64"] = "A" * (20 * 1024 * 1024 * 4 // 3 + 9)
    browser, _ = _browser(tmp_path / "oversized", lambda *_: oversized)
    with pytest.raises(ValueError, match="encoded byte limit"):
        browser.capture(_request())
