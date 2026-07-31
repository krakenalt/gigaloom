"""Typed failures for MCP App admission and host security."""

from __future__ import annotations


class MCPAppError(ValueError):
    """Base error with a stable, content-free reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MCPAppAdmissionError(MCPAppError):
    """A resource cannot enter the admitted MCP App cache."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        denied_evidence: tuple[str, ...] = (),
    ) -> None:
        super().__init__(code, message)
        self.denied_evidence = denied_evidence


class MCPAppCacheError(MCPAppError):
    """A bounded MCP App cache operation failed closed."""


class MCPAppChannelError(MCPAppError):
    """An MCP App bridge message violated its channel contract."""
