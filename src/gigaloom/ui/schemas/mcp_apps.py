"""Bounded HTTP schemas for the MCP App host backend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MCPAppFallbackResponse(BaseModel):
    """Complete typed non-visual response."""

    code: str
    message: str
    textual: str
    structured: dict[str, object] = Field(default_factory=dict)
    denied_evidence: list[str] = Field(default_factory=list)


class MCPAppFrameDescriptorResponse(BaseModel):
    """Browser-facing contract for one admitted isolated frame."""

    instance_id: str
    server_id: str
    resource_sha256: str
    resource_uri: str
    resource_url: str
    sandbox: Literal["allow-scripts"]
    content_security_policy: str
    channel_id: str
    nonce: str
    source_id: str
    initialization: dict[str, object]


class MCPAppFrameResponse(BaseModel):
    """Exactly one admitted descriptor or textual/structured fallback."""

    status: Literal["admitted", "fallback"]
    frame: MCPAppFrameDescriptorResponse | None = None
    fallback: MCPAppFallbackResponse | None = None


class MCPAppFrameCreateRequest(BaseModel):
    """Content-free authority binding for one cached resource."""

    server_id: str = Field(min_length=1, max_length=128)
    tool_id: str = Field(min_length=1, max_length=256)
    resource_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    theme: Literal["dark", "light", "system"] = "system"
    locale: str = Field(default="en", min_length=1, max_length=35)
    display_mode: Literal["inline", "panel"] = "inline"


class MCPAppMessageResponse(BaseModel):
    """Acknowledgement for one validated and completed bridge request."""

    accepted: Literal[True] = True
    request_id: str | int
    method: Literal["ui/ready", "ui/userChoice"]


class MCPAppTeardownResponse(BaseModel):
    """Content-free frame teardown result."""

    destroyed: Literal[True] = True
    cancelled_requests: int = Field(ge=0, le=16)
