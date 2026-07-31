"""Public MCP Apps backend contracts and services."""

from .bridge import MCP_APP_ALLOWED_METHODS, MCPAppBridge
from .cache import MCPAppCacheSnapshot, MCPAppResourceCache
from .contracts import (
    MCP_APPS_EXTENSION_ID,
    MCP_APPS_SPEC_VERSION,
    MCP_APP_HTML_MIME_TYPE,
    AdmittedMCPAppResource,
    MCPAppBridgeRequest,
    MCPAppDisplayContext,
    MCPAppFallback,
    MCPAppFallbackCode,
    MCPAppFrameBinding,
    MCPAppFrameDescriptor,
    MCPAppLimits,
    MCPAppResourceAdmission,
    MCPAppResourceCandidate,
    MCPAppServerIdentity,
)
from .csp import (
    MCP_APP_IFRAME_SANDBOX,
    mcp_app_content_security_policy,
    mcp_app_resource_headers,
)
from .discovery import admit_mcp_app_resource
from .errors import MCPAppAdmissionError, MCPAppCacheError, MCPAppChannelError
from .evidence import MCPAppEvidenceLog, MCPAppSecurityEvent
from .sessions import MCPAppSessionRegistry

__all__ = [
    "MCP_APPS_EXTENSION_ID",
    "MCP_APPS_SPEC_VERSION",
    "MCP_APP_HTML_MIME_TYPE",
    "AdmittedMCPAppResource",
    "MCP_APP_ALLOWED_METHODS",
    "MCP_APP_IFRAME_SANDBOX",
    "MCPAppAdmissionError",
    "MCPAppCacheError",
    "MCPAppCacheSnapshot",
    "MCPAppChannelError",
    "MCPAppBridge",
    "MCPAppBridgeRequest",
    "MCPAppDisplayContext",
    "MCPAppEvidenceLog",
    "MCPAppFallback",
    "MCPAppFallbackCode",
    "MCPAppFrameBinding",
    "MCPAppFrameDescriptor",
    "MCPAppLimits",
    "MCPAppResourceAdmission",
    "MCPAppResourceCache",
    "MCPAppResourceCandidate",
    "MCPAppServerIdentity",
    "MCPAppSecurityEvent",
    "MCPAppSessionRegistry",
    "admit_mcp_app_resource",
    "mcp_app_content_security_policy",
    "mcp_app_resource_headers",
]
