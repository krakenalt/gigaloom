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
        if self.max_app_instance_state_bytes < 3:
            raise ValueError("max_app_instance_state_bytes must be at least 3")


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
    INVALID_FALLBACK = "invalid_fallback"
    RESOURCE_TOO_LARGE = "resource_too_large"
    FALLBACK_TOO_LARGE = "fallback_too_large"
    POLICY_DENIED = "policy_denied"
    CACHE_LIMIT = "cache_limit"
    HOST_LIMIT = "host_limit"
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


@dataclass(frozen=True, slots=True)
class MCPAppFrameBinding:
    """Authority context bound to every message from one app frame."""

    server_id: str
    tool_id: str
    resource_sha256: str
    workspace_id: str
    session_id: str
    run_id: str

    def __post_init__(self) -> None:
        values = (
            self.server_id,
            self.tool_id,
            self.resource_sha256,
            self.workspace_id,
            self.session_id,
            self.run_id,
        )
        if any(not value.strip() for value in values):
            raise ValueError("MCP App frame bindings must not be empty")


@dataclass(frozen=True, slots=True)
class MCPAppDisplayContext:
    """Non-secret display metadata exposed during frame initialization."""

    theme: str = "system"
    locale: str = "en"
    display_mode: str = "inline"

    def __post_init__(self) -> None:
        if self.theme not in {"dark", "light", "system"}:
            raise ValueError("unsupported MCP App theme")
        if self.display_mode not in {"inline", "panel"}:
            raise ValueError("unsupported MCP App display mode")
        if not self.locale or len(self.locale) > 35:
            raise ValueError("invalid MCP App locale")


@dataclass(frozen=True, slots=True)
class MCPAppFrameDescriptor:
    """Content-free data required to construct one isolated iframe."""

    instance_id: str
    server_id: str
    resource_sha256: str
    resource_uri: str
    sandbox: str
    content_security_policy: str
    channel_id: str
    nonce: str
    source_id: str
    initialization: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MCPAppBridgeRequest:
    """Validated app-to-host JSON-RPC request."""

    request_id: str | int
    method: str
    params: Mapping[str, object]
    binding: MCPAppFrameBinding
