"""Bounded HTTPS transport for an explicitly requested registry refresh."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gigaloom.harnesses.agent_profiles.registry.errors import (
    RegistryNetworkError,
    RegistryResponseError,
)
from gigaloom.harnesses.agent_profiles.registry.schema import (
    MAX_REGISTRY_DOCUMENT_BYTES,
)


DEFAULT_REGISTRY_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class RegistryFetchRequest:
    """One exact, bounded, conditional registry GET request."""

    url: str
    headers: Mapping[str, str]
    timeout_seconds: float = DEFAULT_REGISTRY_TIMEOUT_SECONDS
    max_bytes: int = MAX_REGISTRY_DOCUMENT_BYTES

    def __post_init__(self) -> None:
        if not self.url.startswith("https://"):
            raise ValueError("registry fetch URL must use HTTPS")
        if not isinstance(self.headers, Mapping):
            raise ValueError("registry fetch headers must be a mapping")
        headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key
                or not value
                or len(key) > 128
                or len(value) > 1_024
                or "\r" in key
                or "\n" in key
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("registry fetch header is invalid")
            headers[key] = value
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("registry fetch timeout is invalid")
        if not 1 <= self.max_bytes <= MAX_REGISTRY_DOCUMENT_BYTES:
            raise ValueError("registry fetch byte limit is invalid")
        object.__setattr__(self, "headers", MappingProxyType(headers))


@dataclass(frozen=True, slots=True)
class RegistryFetchResponse:
    """Bounded response bytes plus exact final URL and inert headers."""

    status_code: int
    final_url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def __post_init__(self) -> None:
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("registry response status is invalid")
        if not isinstance(self.final_url, str) or not self.final_url.startswith(
            "https://"
        ):
            raise ValueError("registry response final URL is invalid")
        if not isinstance(self.headers, tuple) or len(self.headers) > 64:
            raise ValueError("registry response headers are invalid")
        normalized: list[tuple[str, str]] = []
        for item in self.headers:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(value, str) for value in item)
            ):
                raise ValueError("registry response header is invalid")
            key, value = item
            if not key or len(key) > 128 or len(value) > 1_024:
                raise ValueError("registry response header is invalid")
            normalized.append((key.lower(), value))
        if len({key for key, _ in normalized}) != len(normalized):
            raise ValueError("registry response headers must be unique")
        if (
            not isinstance(self.body, bytes)
            or len(self.body) > MAX_REGISTRY_DOCUMENT_BYTES
        ):
            raise ValueError("registry response body is invalid or too large")
        object.__setattr__(self, "headers", tuple(sorted(normalized)))

    def header(self, name: str) -> str | None:
        """Return one case-insensitive response header."""
        requested = name.lower()
        return next(
            (value for key, value in self.headers if key == requested),
            None,
        )


class RegistryTransport(Protocol):
    """Transport port; tests provide a fully hermetic fake."""

    def fetch(self, request: RegistryFetchRequest) -> RegistryFetchResponse:
        """Fetch exactly one explicitly requested registry document."""


class UrllibACPRegistryTransport:
    """Standard-library implementation with a strict bounded response read."""

    def fetch(self, request: RegistryFetchRequest) -> RegistryFetchResponse:
        """Issue one GET and retain no cookies, credentials, or ambient headers."""
        outbound = Request(
            request.url,
            headers=dict(request.headers),
            method="GET",
        )
        opener = build_opener(_RejectRedirects())
        try:
            with opener.open(outbound, timeout=request.timeout_seconds) as response:
                body = response.read(request.max_bytes + 1)
                if len(body) > request.max_bytes:
                    raise RegistryResponseError("ACP registry response is too large")
                return RegistryFetchResponse(
                    status_code=response.status,
                    final_url=response.geturl(),
                    headers=_selected_headers(response.headers),
                    body=body,
                )
        except HTTPError as error:
            if error.code == 304:
                return RegistryFetchResponse(
                    status_code=304,
                    final_url=error.geturl(),
                    headers=_selected_headers(error.headers),
                    body=b"",
                )
            raise RegistryResponseError(
                f"ACP registry returned HTTP {error.code}"
            ) from error
        except RegistryResponseError:
            raise
        except (OSError, TimeoutError, URLError) as error:
            raise RegistryNetworkError("ACP registry network request failed") from error


def _selected_headers(headers: object) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for name in ("Content-Type", "ETag", "Last-Modified"):
        value = headers.get(name) if hasattr(headers, "get") else None
        if isinstance(value, str) and value:
            result.append((name, value))
    return tuple(result)


class _RejectRedirects(HTTPRedirectHandler):
    """Prevent a canonical registry GET from contacting a redirected origin."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        del request, file_pointer, code, message, headers, new_url
        return None
