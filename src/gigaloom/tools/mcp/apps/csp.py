"""Restrictive frame sandbox and response policy for MCP App HTML."""

from __future__ import annotations

from typing import Mapping

MCP_APP_IFRAME_SANDBOX = "allow-scripts"

_CSP_DIRECTIVES = (
    "default-src 'none'",
    "base-uri 'none'",
    "connect-src 'none'",
    "font-src 'none'",
    "form-action 'none'",
    "frame-ancestors 'self'",
    "frame-src 'none'",
    "img-src 'none'",
    "media-src 'none'",
    "object-src 'none'",
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    "worker-src 'none'",
)


def mcp_app_content_security_policy() -> str:
    """Return the pinned v1 CSP; resource metadata cannot relax it."""
    return "; ".join(_CSP_DIRECTIVES)


def mcp_app_resource_headers() -> Mapping[str, str]:
    """Return browser isolation headers for an admitted HTML response."""
    return {
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": mcp_app_content_security_policy(),
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), clipboard-read=(), "
            "clipboard-write=(), display-capture=(), fullscreen=(), payment=()"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
    }
