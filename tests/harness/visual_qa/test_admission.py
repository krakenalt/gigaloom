"""Admission tests for the bounded local Visual QA browser contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from gigaloom.automation.evaluations.visual.api import (
    BrowserCaptureRequest,
    BrowserFingerprint,
    VisualProcessNetworkGrant,
    admit_visual_target,
    validate_navigation_url,
)
from gigaloom.contracts import VisualViewportV1


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _grant(origin: str = "http://127.0.0.1:3000") -> VisualProcessNetworkGrant:
    return VisualProcessNetworkGrant(
        grant_id="visual-grant-1",
        process_id=42,
        origin=origin,
        source_revision="deadbeef",
        network_policy_digest=_digest("deny-external"),
    )


def _browser() -> BrowserFingerprint:
    return BrowserFingerprint(
        engine="chromium",
        browser_version="140.0.7339.16",
        executable_digest=_digest("chromium-executable"),
        automation_name="playwright",
        automation_version="1.61.0",
    )


def test_admission_binds_exact_process_origin_viewports_and_browser():
    admission = admit_visual_target(
        target_url="http://127.0.0.1:3000/app",
        grant=_grant(),
        browser=_browser(),
    )

    assert admission.grant.process_id == 42
    assert admission.browser.digest == _browser().digest
    assert [(item.width, item.height) for item in admission.viewports] == [
        (1440, 900),
        (390, 844),
    ]
    assert admission.fresh_profile is True
    assert len(admission.origin_policy_digest) == 64
    assert (
        BrowserCaptureRequest(
            admission=admission,
            viewport=admission.viewports[0],
        ).viewport.viewport_id
        == "desktop"
    )


@pytest.mark.parametrize(
    "target_url",
    (
        "https://example.com/app",
        "http://user:password@127.0.0.1:3000/app",
        "http://127.0.0.1:3000/app?token=secret",
        "http://127.0.0.1:3000/token/secret-value",
    ),
)
def test_admission_rejects_remote_or_secret_bearing_urls(target_url: str):
    with pytest.raises(ValueError, match="secret-free|exact origin"):
        admit_visual_target(
            target_url=target_url,
            grant=_grant(),
            browser=_browser(),
        )


def test_admission_rejects_ungranted_viewports_profile_reuse_and_weak_grant():
    with pytest.raises(ValueError, match="desktop and 390x844"):
        admit_visual_target(
            target_url="http://127.0.0.1:3000/app",
            grant=_grant(),
            browser=_browser(),
            viewports=(
                VisualViewportV1(viewport_id="desktop", width=1280, height=720),
                VisualViewportV1(viewport_id="mobile", width=390, height=844),
            ),
        )
    admission = admit_visual_target(
        target_url="http://127.0.0.1:3000/app",
        grant=_grant(),
        browser=_browser(),
    )
    with pytest.raises(ValueError, match="profile reuse"):
        replace(admission, fresh_profile=False)
    with pytest.raises(ValueError, match="exact-origin"):
        replace(_grant(), exact_origin_only=False)


def test_navigation_allows_same_origin_redirect_and_blocks_remote_hop():
    admission = admit_visual_target(
        target_url="http://127.0.0.1:3000/app",
        grant=_grant(),
        browser=_browser(),
    )

    assert (
        validate_navigation_url(
            admission,
            "http://127.0.0.1:3000/app/ready",
            redirects=("http://127.0.0.1:3000/loading",),
        )
        == "http://127.0.0.1:3000/app/ready"
    )
    with pytest.raises(ValueError, match="loopback-only"):
        validate_navigation_url(
            admission,
            "https://example.com/steal",
            redirects=("http://127.0.0.1:3000/loading",),
        )


def test_capture_request_cannot_smuggle_an_unadmitted_viewport():
    admission = admit_visual_target(
        target_url="http://127.0.0.1:3000/app",
        grant=_grant(),
        browser=_browser(),
    )
    with pytest.raises(ValueError, match="not admitted"):
        BrowserCaptureRequest(
            admission=admission,
            viewport=VisualViewportV1(
                viewport_id="tablet",
                width=768,
                height=1024,
            ),
        )
