"""Fail-closed admission of discovered MCP App resource metadata."""

from __future__ import annotations

from .contracts import (
    MCP_APPS_EXTENSION_ID,
    MCP_APPS_SPEC_VERSION,
    MCPAppFallback,
    MCPAppFallbackCode,
    MCPAppLimits,
    MCPAppResourceAdmission,
    MCPAppResourceCandidate,
)
from .errors import MCPAppAdmissionError
from .resources import validate_resource_candidate


def admit_mcp_app_resource(
    candidate: MCPAppResourceCandidate,
    *,
    limits: MCPAppLimits | None = None,
) -> MCPAppResourceAdmission:
    """Admit pinned extension metadata or return its complete fallback."""
    limits = limits or MCPAppLimits()
    try:
        if candidate.extension_id != MCP_APPS_EXTENSION_ID:
            raise MCPAppAdmissionError(
                MCPAppFallbackCode.UNSUPPORTED_EXTENSION.value,
                "MCP App extension is not the pinned stable extension",
            )
        if candidate.specification_version != MCP_APPS_SPEC_VERSION:
            raise MCPAppAdmissionError(
                MCPAppFallbackCode.UNSUPPORTED_SPECIFICATION.value,
                "MCP App specification version is not supported",
            )
        resource = validate_resource_candidate(candidate, limits=limits)
    except MCPAppAdmissionError as exc:
        return MCPAppResourceAdmission(
            fallback=MCPAppFallback(
                code=MCPAppFallbackCode(exc.code),
                message=str(exc),
                textual=candidate.textual_fallback,
                structured=dict(candidate.structured_fallback),
            )
        )
    return MCPAppResourceAdmission(resource=resource)
