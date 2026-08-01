"""Bounded HTTPS byte transport for managed binary distributions."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
from typing import Iterable, Protocol, runtime_checkable
import urllib.error
import urllib.request

from gigaloom.harnesses.agent_profiles.installations.errors import AgentInstallError


DEFAULT_BINARY_TIMEOUT_SECONDS = 30.0
DEFAULT_BINARY_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class BinaryDownloadRequest:
    """One exact bounded HTTPS archive request."""

    url: str
    timeout_seconds: float
    max_bytes: int


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
        """Return one response without following redirects."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, ARG002
        return None


class UrllibBinaryDownloadTransport:
    """Production stdlib transport with redirect and byte bounds."""

    def fetch(self, request: BinaryDownloadRequest) -> BinaryDownloadResponse:
        if request.timeout_seconds <= 0 or request.max_bytes <= 0:
            raise ValueError("binary download bounds are invalid")
        opener = urllib.request.build_opener(_RejectRedirects())
        http_request = urllib.request.Request(
            request.url,
            headers={"Accept": "application/octet-stream"},
            method="GET",
        )
        try:
            response = opener.open(http_request, timeout=request.timeout_seconds)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise AgentInstallError("binary_download_failed") from error
        status = response.getcode()
        final_url = response.geturl()
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
