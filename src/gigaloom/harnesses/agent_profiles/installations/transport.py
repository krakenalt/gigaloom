"""Bounded HTTPS byte transport for managed binary distributions."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
from typing import Iterable, Protocol, runtime_checkable
import urllib.error
from urllib.parse import urlsplit
import urllib.request

from gigaloom.harnesses.agent_profiles.installations.errors import AgentInstallError


DEFAULT_BINARY_TIMEOUT_SECONDS = 30.0
DEFAULT_BINARY_CHUNK_BYTES = 64 * 1024
DEFAULT_BINARY_MAX_REDIRECTS = 2


@dataclass(frozen=True, slots=True)
class BinaryDownloadRequest:
    """One exact bounded HTTPS archive request."""

    url: str
    timeout_seconds: float
    max_bytes: int
    allowed_origins: tuple[str, ...]
    max_redirects: int = DEFAULT_BINARY_MAX_REDIRECTS


@dataclass(frozen=True, slots=True)
class BinaryDownloadResponse:
    """Streaming response metadata with an owned bounded body iterator."""

    status_code: int
    final_url: str
    content_length: int | None
    chunks: Iterable[bytes]


@runtime_checkable
class BinaryDownloadTransport(Protocol):
    """Injected archive transport; tests never require live downloads."""

    def fetch(self, request: BinaryDownloadRequest) -> BinaryDownloadResponse:
        """Return one response within the request's bounded origin authority."""


class _BoundedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow only HTTPS redirects whose origins were bound into the plan."""

    def __init__(
        self,
        allowed_origins: frozenset[str],
        max_redirects: int,
    ) -> None:
        super().__init__()
        self._allowed_origins = allowed_origins
        self._max_redirects = max_redirects
        self._redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        try:
            current_origin = _https_url_origin(req.full_url)
            redirect_origin = _https_url_origin(newurl)
        except ValueError as error:
            raise AgentInstallError("binary_download_redirect_rejected") from error
        if (
            current_origin not in self._allowed_origins
            or redirect_origin not in self._allowed_origins
        ):
            raise AgentInstallError("binary_download_redirect_rejected")
        if self._redirect_count >= self._max_redirects:
            raise AgentInstallError("binary_download_redirect_limit_exceeded")
        self._redirect_count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibBinaryDownloadTransport:
    """Production stdlib transport with redirect and byte bounds."""

    def fetch(self, request: BinaryDownloadRequest) -> BinaryDownloadResponse:
        if (
            request.timeout_seconds <= 0
            or request.max_bytes <= 0
            or request.max_redirects < 0
        ):
            raise ValueError("binary download bounds are invalid")
        allowed_origins = _validated_allowed_origins(request.allowed_origins)
        if _https_url_origin(request.url) not in allowed_origins:
            raise AgentInstallError("binary_download_origin_not_allowed")
        opener = urllib.request.build_opener(
            _BoundedRedirects(allowed_origins, request.max_redirects)
        )
        http_request = urllib.request.Request(
            request.url,
            headers={"Accept": "application/octet-stream"},
            method="GET",
        )
        try:
            response = opener.open(http_request, timeout=request.timeout_seconds)
        except AgentInstallError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise AgentInstallError("binary_download_failed") from error
        status = response.getcode()
        final_url = response.geturl()
        try:
            final_origin = _https_url_origin(final_url)
        except ValueError as error:
            response.close()
            raise AgentInstallError("binary_download_response_invalid") from error
        if final_origin not in allowed_origins:
            response.close()
            raise AgentInstallError("binary_download_redirect_rejected")
        length_header = response.headers.get("Content-Length")
        try:
            content_length = int(length_header) if length_header is not None else None
        except ValueError as error:
            response.close()
            raise AgentInstallError("binary_content_length_invalid") from error
        if content_length is not None and not 0 <= content_length <= request.max_bytes:
            response.close()
            raise AgentInstallError("binary_download_too_large")

        def chunks() -> Iterable[bytes]:
            received = 0
            try:
                while True:
                    chunk = response.read(DEFAULT_BINARY_CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > request.max_bytes:
                        raise AgentInstallError("binary_download_too_large")
                    yield chunk
            finally:
                response.close()

        return BinaryDownloadResponse(
            status_code=status,
            final_url=final_url,
            content_length=content_length,
            chunks=chunks(),
        )


def is_binary_download_url_allowed(
    url: str,
    allowed_origins: tuple[str, ...],
) -> bool:
    """Return whether an HTTPS URL is covered by exact normalized origins."""
    try:
        return _https_url_origin(url) in _validated_allowed_origins(allowed_origins)
    except (TypeError, ValueError):
        return False


def _validated_allowed_origins(values: tuple[str, ...]) -> frozenset[str]:
    if not isinstance(values, tuple) or not values or len(values) > 32:
        raise ValueError("binary download origins are invalid")
    normalized = tuple(_https_origin(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError("binary download origins must be unique")
    return frozenset(normalized)


def _https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"} or parsed.query:
        raise ValueError("binary download origin must be exact")
    return _https_url_origin(value)


def _https_url_origin(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("binary download URL must be text")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("binary download URL port is invalid") from error
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("binary download URL must be credential-free HTTPS")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    suffix = "" if port in {None, 443} else f":{port}"
    return f"https://{host}{suffix}"
