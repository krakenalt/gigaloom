"""Versioned, content-free contracts for the MCP Apps backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

MCP_APPS_SPEC_VERSION = "2026-01-26"
MCP_APPS_EXTENSION_ID = "io.modelcontextprotocol/ui"
MCP_APP_HTML_MIME_TYPE = "text/html;profile=mcp-app"

MAX_HTML_RESOURCE_BYTES = 1024 * 1024
MAX_APP_INSTANCE_STATE_BYTES = 512 * 1024
MAX_CACHED_RESOURCES_PER_SERVER = 32
MAX_WORKSPACE_CACHE_BYTES = 16 * 1024 * 1024
MAX_POST_MESSAGE_BYTES = 256 * 1024
MAX_OUTSTANDING_APP_REQUESTS = 16


@dataclass(frozen=True, slots=True)
class MCPAppLimits:
    """Release ceilings that callers may only tighten."""

    max_html_resource_bytes: int = MAX_HTML_RESOURCE_BYTES
    max_app_instance_state_bytes: int = MAX_APP_INSTANCE_STATE_BYTES
    max_cached_resources_per_server: int = MAX_CACHED_RESOURCES_PER_SERVER
    max_workspace_cache_bytes: int = MAX_WORKSPACE_CACHE_BYTES
    max_post_message_bytes: int = MAX_POST_MESSAGE_BYTES
    max_outstanding_app_requests: int = MAX_OUTSTANDING_APP_REQUESTS

    def __post_init__(self) -> None:
        ceilings = {
            "max_html_resource_bytes": MAX_HTML_RESOURCE_BYTES,
            "max_app_instance_state_bytes": MAX_APP_INSTANCE_STATE_BYTES,
            "max_cached_resources_per_server": MAX_CACHED_RESOURCES_PER_SERVER,
            "max_workspace_cache_bytes": MAX_WORKSPACE_CACHE_BYTES,
            "max_post_message_bytes": MAX_POST_MESSAGE_BYTES,
            "max_outstanding_app_requests": MAX_OUTSTANDING_APP_REQUESTS,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if value <= 0 or value > ceiling:
                raise ValueError(f"{name} must be between 1 and {ceiling}")


@dataclass(frozen=True, slots=True)
class MCPAppServerIdentity:
    """Security-relevant identity of the MCP server supplying an app."""

    server_id: str
    local: bool
    trusted: bool
    read_only: bool


@dataclass(frozen=True, slots=True)
class MCPAppResourceCandidate:
    """One discovered UI resource plus its complete non-visual fallback."""

    server: MCPAppServerIdentity
    uri: str
    mime_type: str
    html: bytes
    expected_sha256: str
    textual_fallback: str
    structured_fallback: Mapping[str, object] = field(default_factory=dict)
    specification_version: str = MCP_APPS_SPEC_VERSION
    extension_id: str = MCP_APPS_EXTENSION_ID
    requested_permissions: tuple[str, ...] = ()
    requested_connect_domains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdmittedMCPAppResource:
    """Immutable content-addressed HTML accepted for later frame creation."""

    server_id: str
    uri: str
    mime_type: str
    sha256: str
    html: bytes = field(repr=False)
    textual_fallback: str
    structured_fallback: Mapping[str, object]

    @property
    def size_bytes(self) -> int:
        """Return the admitted uncompressed byte size."""
        return len(self.html)


class MCPAppFallbackCode(str, Enum):
    """Stable reasons for declining visual rendering."""

    SERVER_NOT_LOCAL = "server_not_local"
    SERVER_NOT_TRUSTED = "server_not_trusted"
    SERVER_NOT_READ_ONLY = "server_not_read_only"
    UNSUPPORTED_EXTENSION = "unsupported_extension"
    UNSUPPORTED_SPECIFICATION = "unsupported_specification"
    INVALID_URI = "invalid_uri"
    INVALID_MIME_TYPE = "invalid_mime_type"
    INVALID_DIGEST = "invalid_digest"
    DIGEST_MISMATCH = "digest_mismatch"
    INVALID_HTML = "invalid_html"
    RESOURCE_TOO_LARGE = "resource_too_large"
    FALLBACK_TOO_LARGE = "fallback_too_large"
    POLICY_DENIED = "policy_denied"
    CACHE_LIMIT = "cache_limit"
    RESOURCE_UNAVAILABLE = "resource_unavailable"


@dataclass(frozen=True, slots=True)
class MCPAppFallback:
    """Complete non-visual response returned when rendering fails closed."""

    code: MCPAppFallbackCode
    message: str
    textual: str
    structured: Mapping[str, object]
    denied_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MCPAppResourceAdmission:
    """Exactly one admitted resource or typed fallback."""

    resource: AdmittedMCPAppResource | None = None
    fallback: MCPAppFallback | None = None

    def __post_init__(self) -> None:
        if (self.resource is None) == (self.fallback is None):
            raise ValueError("admission must contain exactly one outcome")

    @property
    def admitted(self) -> bool:
        """Return whether the visual resource passed admission."""
        return self.resource is not None
