"""Fail-closed admission for one local Visual QA browser target."""

from __future__ import annotations

from collections.abc import Iterable

from gigaloom.automation.evaluations.visual.contracts import (
    BrowserFingerprint,
    VisualBrowserAdmission,
    VisualProcessNetworkGrant,
    _url_origin,
    _validate_secret_free_target_url,
)
from gigaloom.contracts import VisualViewportV1


def admit_visual_target(
    *,
    target_url: str,
    grant: VisualProcessNetworkGrant,
    browser: BrowserFingerprint,
    viewports: tuple[VisualViewportV1, ...] | None = None,
    browser_lifetime_ms: int = 30_000,
    max_requests: int = 128,
    max_redirects: int = 4,
) -> VisualBrowserAdmission:
    """Admit a secret-free URL under one exact process/network grant."""
    kwargs: dict[str, object] = {}
    if viewports is not None:
        kwargs["viewports"] = viewports
    return VisualBrowserAdmission(
        target_url=target_url,
        grant=grant,
        browser=browser,
        browser_lifetime_ms=browser_lifetime_ms,
        max_requests=max_requests,
        max_redirects=max_redirects,
        **kwargs,
    )


def validate_navigation_url(
    admission: VisualBrowserAdmission,
    final_url: str,
    *,
    redirects: Iterable[str] = (),
) -> str:
    """Reject secret-bearing or cross-origin navigation at every redirect hop."""
    chain = tuple(redirects)
    if len(chain) > admission.max_redirects:
        raise ValueError("visual navigation exceeded the redirect limit")
    for item in (*chain, final_url):
        _validate_secret_free_target_url(item)
        if _url_origin(item) != admission.grant.origin:
            raise ValueError("visual navigation escaped the exact origin grant")
    return final_url


__all__ = ["admit_visual_target", "validate_navigation_url"]
