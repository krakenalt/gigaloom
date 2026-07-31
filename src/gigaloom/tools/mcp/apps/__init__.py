"""Public MCP Apps backend contracts and services."""

from .cache import MCPAppCacheSnapshot, MCPAppResourceCache
from .contracts import (
    MCP_APPS_EXTENSION_ID,
    MCP_APPS_SPEC_VERSION,
    MCP_APP_HTML_MIME_TYPE,
    AdmittedMCPAppResource,
    MCPAppFallback,
    MCPAppFallbackCode,
    MCPAppLimits,
    MCPAppResourceAdmission,
    MCPAppResourceCandidate,
    MCPAppServerIdentity,
)
from .discovery import admit_mcp_app_resource
from .errors import MCPAppAdmissionError, MCPAppCacheError, MCPAppChannelError

__all__ = [
    "MCP_APPS_EXTENSION_ID",
    "MCP_APPS_SPEC_VERSION",
    "MCP_APP_HTML_MIME_TYPE",
    "AdmittedMCPAppResource",
    "MCPAppAdmissionError",
    "MCPAppCacheError",
    "MCPAppCacheSnapshot",
    "MCPAppChannelError",
    "MCPAppFallback",
    "MCPAppFallbackCode",
    "MCPAppLimits",
    "MCPAppResourceAdmission",
    "MCPAppResourceCache",
    "MCPAppResourceCandidate",
    "MCPAppServerIdentity",
    "admit_mcp_app_resource",
]
