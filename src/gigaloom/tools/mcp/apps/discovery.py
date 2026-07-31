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
from .policy import enforce_resource_policy
from .resources import validate_fallback, validate_resource_candidate


def admit_mcp_app_resource(
    candidate: MCPAppResourceCandidate,
    *,
    limits: MCPAppLimits | None = None,
) -> MCPAppResourceAdmission:
    """Admit pinned extension metadata or return its complete fallback."""
    limits = limits or MCPAppLimits()
    try:
        validate_fallback(candidate, limits=limits)
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
        enforce_resource_policy(candidate, resource)
    except MCPAppAdmissionError as exc:
        fallback_invalid = exc.code in {
            MCPAppFallbackCode.FALLBACK_TOO_LARGE.value,
            MCPAppFallbackCode.INVALID_FALLBACK.value,
        }
        safe_textual, safe_structured = _safe_fallback(limits)
        return MCPAppResourceAdmission(
            fallback=MCPAppFallback(
                code=MCPAppFallbackCode(exc.code),
                message=str(exc),
                textual=(
                    safe_textual if fallback_invalid else candidate.textual_fallback
                ),
                structured=(
                    safe_structured
                    if fallback_invalid
                    else dict(candidate.structured_fallback)
                ),
                denied_evidence=tuple(getattr(exc, "denied_evidence", ())),
            )
        )
    return MCPAppResourceAdmission(resource=resource)


def _safe_fallback(limits: MCPAppLimits) -> tuple[str, dict[str, object]]:
    textual = "Interactive view unavailable."
    structured: dict[str, object] = {"status": "fallback_invalid"}
    encoded_size = len(textual.encode()) + len('{"status":"fallback_invalid"}')
    if encoded_size <= limits.max_app_instance_state_bytes:
        return textual, structured
    return "!", {}
