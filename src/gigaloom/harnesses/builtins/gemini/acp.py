"""Gemini ACP contracts, process lifecycle, and protocol facade."""

from __future__ import annotations

from gigaloom.harnesses.builtins.gemini.acp_contracts import (
    AuthProvider,
    Clock,
    GEMINI_ACP_PERMISSION_METHOD,
    GEMINI_ACP_PROTOCOL,
    GEMINI_ACP_PROTOCOL_VERSION,
    GeminiAcpError,
    GeminiAcpHandshake,
    GeminiAcpStdioScope,
    McpProvider,
    create_gemini_acp_stdio_scope,
    probe_gemini_acp_handshake,
)
from gigaloom.harnesses.builtins.gemini.acp_driver import GeminiAcpDriver
from gigaloom.harnesses.builtins.gemini.acp_protocol import (
    normalize_gemini_acp_event,
)

__all__ = [
    "GEMINI_ACP_PERMISSION_METHOD",
    "GEMINI_ACP_PROTOCOL",
    "GEMINI_ACP_PROTOCOL_VERSION",
    "AuthProvider",
    "Clock",
    "GeminiAcpDriver",
    "GeminiAcpError",
    "GeminiAcpHandshake",
    "GeminiAcpStdioScope",
    "McpProvider",
    "create_gemini_acp_stdio_scope",
    "normalize_gemini_acp_event",
    "probe_gemini_acp_handshake",
]
