"""Bounded redirect policy for managed binary downloads."""

from __future__ import annotations

from urllib.request import Request

import pytest

from gigaloom.harnesses.agent_profiles.installations import AgentInstallError
from gigaloom.harnesses.agent_profiles.installations.transport import (
    _BoundedRedirects,
)


SOURCE = (
    "https://github.com/anomalyco/opencode/releases/download/"
    "v1.18.11/opencode-darwin-arm64.zip"
)
ASSET_ORIGIN = "https://release-assets.githubusercontent.com"
ALLOWED_ORIGINS = frozenset(("https://github.com", ASSET_ORIGIN))


def _redirect(
    handler: _BoundedRedirects,
    request: Request,
    target: str,
) -> Request:
    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        target,
    )
    assert isinstance(redirected, Request)
    return redirected


def test_redirect_handler_allows_exact_plan_bound_https_origin() -> None:
    handler = _BoundedRedirects(ALLOWED_ORIGINS, max_redirects=2)
    target = f"{ASSET_ORIGIN}/github-production-release-asset/id?signature=fixture"

    redirected = _redirect(handler, Request(SOURCE, method="GET"), target)

    assert redirected.full_url == target
    assert redirected.get_method() == "GET"


@pytest.mark.parametrize(
    "target",
    [
        "http://release-assets.githubusercontent.com/asset.zip",
        "https://release-assets.githubusercontent.com.evil.test/asset.zip",
        "https://user@release-assets.githubusercontent.com/asset.zip",
        "https://unreviewed.example.test/asset.zip",
    ],
)
def test_redirect_handler_rejects_unbound_or_unsafe_targets(target: str) -> None:
    handler = _BoundedRedirects(ALLOWED_ORIGINS, max_redirects=2)

    with pytest.raises(AgentInstallError) as captured:
        _redirect(handler, Request(SOURCE, method="GET"), target)

    assert captured.value.reason_code == "binary_download_redirect_rejected"


def test_redirect_handler_enforces_hop_limit() -> None:
    handler = _BoundedRedirects(ALLOWED_ORIGINS, max_redirects=1)
    first = _redirect(
        handler,
        Request(SOURCE, method="GET"),
        f"{ASSET_ORIGIN}/asset-one?signature=fixture",
    )

    with pytest.raises(AgentInstallError) as captured:
        _redirect(
            handler,
            first,
            f"{ASSET_ORIGIN}/asset-two?signature=fixture",
        )

    assert captured.value.reason_code == "binary_download_redirect_limit_exceeded"
