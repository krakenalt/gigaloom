"""Local URI, digest, MIME, and size validation for MCP App resources."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

from .contracts import (
    MCP_APP_HTML_MIME_TYPE,
    AdmittedMCPAppResource,
    MCPAppFallbackCode,
    MCPAppLimits,
    MCPAppResourceCandidate,
)
from .errors import MCPAppAdmissionError

_SERVER_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def validate_resource_candidate(
    candidate: MCPAppResourceCandidate,
    *,
    limits: MCPAppLimits,
) -> AdmittedMCPAppResource:
    """Validate the transport-neutral, content-addressed resource boundary."""
    _validate_server(candidate)
    _validate_uri(candidate.uri, candidate.server.server_id)
    if candidate.mime_type != MCP_APP_HTML_MIME_TYPE:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_MIME_TYPE.value,
            "MCP App resource MIME type is not the pinned HTML profile",
        )
    if not candidate.html:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_HTML.value,
            "MCP App HTML resource must not be empty",
        )
    if len(candidate.html) > limits.max_html_resource_bytes:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.RESOURCE_TOO_LARGE.value,
            "MCP App HTML resource exceeds the configured byte limit",
        )
    try:
        decoded = candidate.html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_HTML.value,
            "MCP App HTML resource must be UTF-8",
        ) from exc
    if "\x00" in decoded:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_HTML.value,
            "MCP App HTML resource contains a null byte",
        )
    digest = candidate.expected_sha256
    if _SHA256_RE.fullmatch(digest) is None:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_DIGEST.value,
            "MCP App resource requires a lowercase SHA-256 digest",
        )
    actual_digest = hashlib.sha256(candidate.html).hexdigest()
    if not hmac.compare_digest(actual_digest, digest):
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.DIGEST_MISMATCH.value,
            "MCP App resource digest does not match its content",
        )
    validate_fallback(candidate, limits=limits)
    return AdmittedMCPAppResource(
        server_id=candidate.server.server_id,
        uri=candidate.uri,
        mime_type=candidate.mime_type,
        sha256=actual_digest,
        html=candidate.html,
        textual_fallback=candidate.textual_fallback,
        structured_fallback=dict(candidate.structured_fallback),
    )


def _validate_server(candidate: MCPAppResourceCandidate) -> None:
    server = candidate.server
    if _SERVER_ID_RE.fullmatch(server.server_id) is None:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_URI.value,
            "MCP App server id is not safe for a ui URI",
        )
    if not server.local:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.SERVER_NOT_LOCAL.value,
            "MCP Apps v1 accepts local servers only",
        )
    if not server.trusted:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.SERVER_NOT_TRUSTED.value,
            "MCP Apps v1 accepts admitted trusted servers only",
        )
    if not server.read_only:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.SERVER_NOT_READ_ONLY.value,
            "MCP Apps v1 accepts read-only integrations only",
        )


def _validate_uri(uri: str, server_id: str) -> None:
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as exc:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_URI.value,
            "MCP App resource requires a canonical server-bound ui URI",
        ) from exc
    unsafe = (
        parsed.scheme != "ui"
        or parsed.netloc != server_id
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or parsed.query
        or parsed.fragment
        or "%" in uri
        or "\\" in uri
        or "//" in parsed.path
        or any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:])
    )
    if unsafe:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_URI.value,
            "MCP App resource requires a canonical server-bound ui URI",
        )


def validate_fallback(
    candidate: MCPAppResourceCandidate,
    *,
    limits: MCPAppLimits,
) -> None:
    """Require a bounded JSON-serializable fallback before any visual decision."""
    if not candidate.textual_fallback.strip():
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_FALLBACK.value,
            "MCP App resource requires a complete textual fallback",
        )
    try:
        encoded = json.dumps(
            candidate.structured_fallback,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.INVALID_FALLBACK.value,
            "MCP App structured fallback must be JSON serializable",
        ) from exc
    total_size = len(candidate.textual_fallback.encode("utf-8")) + len(encoded)
    if total_size > limits.max_app_instance_state_bytes:
        raise MCPAppAdmissionError(
            MCPAppFallbackCode.FALLBACK_TOO_LARGE.value,
            "MCP App fallback exceeds the configured state byte limit",
        )
