"""Stable MCP transport and probe contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit

from gigaloom.secrets import SecretReference
from gigaloom.tools import ToolDescriptor, ToolExecutionPolicy


class MCPTransport(str, Enum):
    """Supported MCP connection transports."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class MCPProbeStatus(str, Enum):
    """Stable health result for one connection probe."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ToolServerDescriptor:
    """MCP specialization of the shared tool provider contract."""

    id: str
    title: str
    transport: MCPTransport
    description: str = ""
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    url: str | None = None
    environment: Mapping[str, str | SecretReference] = field(default_factory=dict)
    headers: Mapping[str, str | SecretReference] = field(default_factory=dict)
    instructions: str = ""
    source: str = "project"
    trusted: bool = False
    enabled: bool = False
    timeout_seconds: float = 10.0
    harnesses: tuple[str, ...] = ()
    execution_policy: ToolExecutionPolicy = field(
        default_factory=lambda: ToolExecutionPolicy(id="default")
    )
    tools: tuple[ToolDescriptor, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.title.strip():
            raise ValueError("MCP server id and title must not be empty")
        if self.transport is MCPTransport.STDIO and not self.command:
            raise ValueError("stdio MCP servers require command")
        if (
            self.transport in {MCPTransport.STREAMABLE_HTTP, MCPTransport.SSE}
            and not self.url
        ):
            raise ValueError("remote MCP servers require url")
        if self.url:
            parsed_url = urlsplit(self.url)
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.hostname
                or parsed_url.username
                or parsed_url.password
                or parsed_url.fragment
            ):
                raise ValueError(
                    "MCP url must be an http(s) endpoint without userinfo or fragment"
                )
        if any(_is_sensitive_arg(item) for item in self.args):
            raise ValueError("sensitive MCP args must use an env/header secret_ref")
        if self.timeout_seconds <= 0:
            raise ValueError("MCP timeout_seconds must be positive")

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        """Return the most recently discovered tools."""
        return self.tools


@dataclass(frozen=True)
class MCPProbeResult:
    """One bounded, redaction-safe discovery result."""

    id: str
    server_id: str
    status: MCPProbeStatus
    started_at: str
    duration_ms: int
    error: str | None = None
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    instructions: str | None = None
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    tools: tuple[ToolDescriptor, ...] = ()
    resources: tuple[Mapping[str, Any], ...] = ()
    prompts: tuple[Mapping[str, Any], ...] = ()


def _is_sensitive_arg(value: str) -> bool:
    option = value.split("=", 1)[0].lstrip("-")
    normalized = option.lower().replace("-", "_")
    markers = (
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
        "api_key",
        "cookie",
    )
    return bool(option) and any(
        normalized == marker
        or normalized.startswith(f"{marker}_")
        or normalized.endswith(f"_{marker}")
        for marker in markers
    )
