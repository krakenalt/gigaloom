"""Authenticated, bounded machine-contract transport for gateway commands."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.native.launch.gateway_discovery import MAX_GATEWAY_DISCOVERY_BYTES


class AuthenticatedGatewayMachineTransport:
    """GET-only transport carrying only the configured gateway key."""

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key
        self.credential_fingerprint = canonical_digest(
            {"gateway_api_key": api_key or ""}
        )

    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        timeout_seconds: float,
    ) -> tuple[int, object]:
        if path not in {"/health", "/models", "/bridge/capabilities"}:
            raise ValueError("gateway discovery path is not admitted")
        headers = {"accept": "application/json"}
        if self.api_key and self.api_key != "0":
            headers.update(
                {
                    "authorization": f"Bearer {self.api_key}",
                    "x-api-key": self.api_key,
                }
            )
        request = Request(
            urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
            headers=headers,
            method="GET",
        )
        try:
            with build_opener(_NoRedirectHandler()).open(
                request,
                timeout=timeout_seconds,
            ) as response:
                return int(response.status), _read_json(response)
        except HTTPError as error:
            error.read(MAX_GATEWAY_DISCOVERY_BYTES + 1)
            return int(error.code), None
        except (OSError, TimeoutError, URLError) as error:
            raise RuntimeError("gateway machine contract is unavailable") from error


def is_managed_gateway_endpoint(base_url: str) -> bool:
    """Return whether an endpoint is an admitted managed loopback root."""
    parsed = urlsplit(base_url)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and not parsed.path.rstrip("/")
    )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


def _read_json(response: Any) -> object:
    data = response.read(MAX_GATEWAY_DISCOVERY_BYTES + 1)
    if len(data) > MAX_GATEWAY_DISCOVERY_BYTES:
        raise ValueError("gateway machine contract exceeds the size bound")
    return json.loads(data.decode("utf-8")) if data else None


__all__ = [
    "AuthenticatedGatewayMachineTransport",
    "is_managed_gateway_endpoint",
]
