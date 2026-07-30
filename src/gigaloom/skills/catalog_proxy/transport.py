"""Fixed-origin skills.sh proxy transport and OIDC boundary."""

from __future__ import annotations

from collections.abc import Mapping
import os
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import anyio

from gigaloom.skills.catalog_proxy.contracts import (
    SkillsProxyUpstreamResponse,
)


async def fetch_skills_proxy_upstream(
    *,
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_response_bytes: int,
) -> SkillsProxyUpstreamResponse:
    """Fetch one fixed-origin upstream response without following redirects."""
    _validate_upstream_url(url)
    return await anyio.to_thread.run_sync(
        lambda: _read_upstream(
            url=url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
    )


def _read_upstream(
    *,
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    max_response_bytes: int,
) -> SkillsProxyUpstreamResponse:
    request = urllib_request.Request(url, headers=dict(headers), method="GET")
    opener = urllib_request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return SkillsProxyUpstreamResponse(
                status_code=response.status,
                final_url=response.geturl(),
                headers=dict(response.headers.items()),
                body=response.read(max_response_bytes + 1),
            )
    except urllib_error.HTTPError as exc:
        return SkillsProxyUpstreamResponse(
            status_code=exc.code,
            final_url=url,
            headers=dict(exc.headers.items()) if exc.headers is not None else {},
            body=exc.read(max_response_bytes + 1),
            redirected=300 <= exc.code < 400,
        )


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _ProxyFailure(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.headers = dict(headers or {})


async def _environment_oidc_token() -> str:
    token = os.environ.get("VERCEL_OIDC_TOKEN")
    if token is None:
        raise _ProxyFailure(503, "proxy.oidc_unavailable")
    return token


def _validate_upstream_url(url: str) -> None:
    parsed = urllib_parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "skills.sh"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not (
            parsed.path == "/api/v1/skills" or parsed.path.startswith("/api/v1/skills/")
        )
    ):
        raise ValueError("skills proxy upstream URL is invalid")
