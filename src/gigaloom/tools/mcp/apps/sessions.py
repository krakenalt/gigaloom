"""Lifecycle owner for isolated MCP App frame instances."""

from __future__ import annotations

import secrets
from threading import RLock
from typing import Callable

from .bridge import MCPAppBridge
from .contracts import (
    MCP_APPS_SPEC_VERSION,
    AdmittedMCPAppResource,
    MCPAppDisplayContext,
    MCPAppFrameBinding,
    MCPAppFrameDescriptor,
    MCPAppLimits,
)
from .csp import MCP_APP_IFRAME_SANDBOX, mcp_app_content_security_policy
from .errors import MCPAppChannelError
from .evidence import MCPAppEvidenceLog


class MCPAppSessionRegistry:
    """Create, resolve, and tear down bounded app frame channels."""

    def __init__(
        self,
        *,
        limits: MCPAppLimits | None = None,
        evidence: MCPAppEvidenceLog | None = None,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
        max_instances: int = 128,
    ) -> None:
        if max_instances <= 0 or max_instances > 128:
            raise ValueError("max_instances must be between 1 and 128")
        self._limits = limits or MCPAppLimits()
        self._evidence = evidence or MCPAppEvidenceLog()
        self._token_factory = token_factory
        self._max_instances = max_instances
        self._descriptors: dict[str, MCPAppFrameDescriptor] = {}
        self._bridges: dict[str, MCPAppBridge] = {}
        self._lock = RLock()

    @property
    def evidence(self) -> MCPAppEvidenceLog:
        """Return the content-free audit owner."""
        return self._evidence

    def create(
        self,
        resource: AdmittedMCPAppResource,
        binding: MCPAppFrameBinding,
        *,
        display: MCPAppDisplayContext | None = None,
    ) -> MCPAppFrameDescriptor:
        """Create a uniquely bound opaque-origin frame contract."""
        if (
            resource.server_id != binding.server_id
            or resource.sha256 != binding.resource_sha256
        ):
            raise MCPAppChannelError(
                "binding_mismatch", "MCP App frame binding does not match its resource"
            )
        display = display or MCPAppDisplayContext()
        with self._lock:
            if len(self._bridges) >= self._max_instances:
                raise MCPAppChannelError(
                    "instance_limit", "MCP App frame instance limit reached"
                )
            instance_id = self._unique_instance_id()
            channel_id = self._token_factory(24)
            nonce = self._token_factory(32)
            source_id = self._token_factory(24)
            descriptor = MCPAppFrameDescriptor(
                instance_id=instance_id,
                server_id=resource.server_id,
                resource_sha256=resource.sha256,
                resource_uri=resource.uri,
                sandbox=MCP_APP_IFRAME_SANDBOX,
                content_security_policy=mcp_app_content_security_policy(),
                channel_id=channel_id,
                nonce=nonce,
                source_id=source_id,
                initialization={
                    "protocolVersion": MCP_APPS_SPEC_VERSION,
                    "channelId": channel_id,
                    "nonce": nonce,
                    "display": {
                        "theme": display.theme,
                        "locale": display.locale,
                        "mode": display.display_mode,
                    },
                },
            )
            bridge = MCPAppBridge(
                instance_id=instance_id,
                source_id=source_id,
                channel_id=channel_id,
                nonce=nonce,
                binding=binding,
                limits=self._limits,
                record_event=self._evidence.append,
            )
            self._descriptors[instance_id] = descriptor
            self._bridges[instance_id] = bridge
            return descriptor

    def get_descriptor(self, instance_id: str) -> MCPAppFrameDescriptor | None:
        """Resolve content-free frame metadata."""
        with self._lock:
            return self._descriptors.get(instance_id)

    def get_bridge(self, instance_id: str) -> MCPAppBridge | None:
        """Resolve the bound channel state machine."""
        with self._lock:
            return self._bridges.get(instance_id)

    def teardown(self, instance_id: str) -> tuple[str | int, ...] | None:
        """Destroy a frame and return request ids cancelled by teardown."""
        with self._lock:
            bridge = self._bridges.pop(instance_id, None)
            self._descriptors.pop(instance_id, None)
        return None if bridge is None else bridge.teardown()

    def _unique_instance_id(self) -> str:
        for _attempt in range(4):
            candidate = f"mcpapp_{self._token_factory(18)}"
            if candidate not in self._bridges:
                return candidate
        raise MCPAppChannelError(
            "instance_collision", "Could not allocate a unique MCP App instance id"
        )
