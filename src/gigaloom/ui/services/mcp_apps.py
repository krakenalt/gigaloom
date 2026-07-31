"""Application service composing MCP App admission, cache, and channels."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from gigaloom.tools.mcp.apps import (
    AdmittedMCPAppResource,
    MCPAppBridgeRequest,
    MCPAppCacheError,
    MCPAppChannelError,
    MCPAppDisplayContext,
    MCPAppFallback,
    MCPAppFallbackCode,
    MCPAppFrameBinding,
    MCPAppFrameDescriptor,
    MCPAppLimits,
    MCPAppResourceCache,
    MCPAppResourceCandidate,
    MCPAppSessionRegistry,
    admit_mcp_app_resource,
)


@dataclass(frozen=True, slots=True)
class MCPAppHostOutcome:
    """Exactly one frame descriptor or fallback from the host service."""

    descriptor: MCPAppFrameDescriptor | None = None
    fallback: MCPAppFallback | None = None

    def __post_init__(self) -> None:
        if (self.descriptor is None) == (self.fallback is None):
            raise ValueError("MCP App host outcome must contain exactly one result")


class MCPAppHostService:
    """Bounded backend owner used by the future Web iframe host."""

    def __init__(
        self,
        *,
        limits: MCPAppLimits | None = None,
        cache: MCPAppResourceCache | None = None,
        sessions: MCPAppSessionRegistry | None = None,
    ) -> None:
        self._limits = limits or MCPAppLimits()
        self._cache = cache or MCPAppResourceCache(limits=self._limits)
        self._sessions = sessions or MCPAppSessionRegistry(limits=self._limits)
        self._choices: dict[str, str] = {}
        self._lock = RLock()

    @property
    def max_post_message_bytes(self) -> int:
        """Return the byte ceiling used while streaming an HTTP body."""
        return self._limits.max_post_message_bytes

    @property
    def sessions(self) -> MCPAppSessionRegistry:
        """Return the frame lifecycle owner for evidence inspection."""
        return self._sessions

    def admit(self, candidate: MCPAppResourceCandidate) -> MCPAppFallback | None:
        """Admit and cache one resource, returning fallback on any denial."""
        admission = admit_mcp_app_resource(candidate, limits=self._limits)
        if admission.fallback is not None:
            return admission.fallback
        resource = admission.resource
        assert resource is not None
        try:
            self._cache.put(resource)
        except MCPAppCacheError as exc:
            return MCPAppFallback(
                code=MCPAppFallbackCode.CACHE_LIMIT,
                message=str(exc),
                textual=resource.textual_fallback,
                structured=dict(resource.structured_fallback),
                denied_evidence=(exc.code,),
            )
        return None

    def create_frame(
        self,
        binding: MCPAppFrameBinding,
        *,
        display: MCPAppDisplayContext | None = None,
    ) -> MCPAppHostOutcome:
        """Create a frame only for the exact cached server-bound digest."""
        resource = self._cache.get(binding.server_id, binding.resource_sha256)
        if resource is None:
            return MCPAppHostOutcome(
                fallback=MCPAppFallback(
                    code=MCPAppFallbackCode.RESOURCE_UNAVAILABLE,
                    message="Admitted MCP App resource is unavailable",
                    textual="Interactive view unavailable.",
                    structured={"status": "resource_unavailable"},
                )
            )
        try:
            descriptor = self._sessions.create(resource, binding, display=display)
        except MCPAppChannelError as exc:
            return MCPAppHostOutcome(
                fallback=MCPAppFallback(
                    code=MCPAppFallbackCode.HOST_LIMIT,
                    message=str(exc),
                    textual=resource.textual_fallback,
                    structured=dict(resource.structured_fallback),
                    denied_evidence=(exc.code,),
                )
            )
        return MCPAppHostOutcome(descriptor=descriptor)

    def descriptor(self, instance_id: str) -> MCPAppFrameDescriptor:
        """Resolve a live frame descriptor."""
        descriptor = self._sessions.get_descriptor(instance_id)
        if descriptor is None:
            raise MCPAppChannelError("frame_not_found", "MCP App frame not found")
        return descriptor

    def resource(self, instance_id: str) -> AdmittedMCPAppResource:
        """Resolve HTML only through a live frame's exact binding."""
        descriptor = self.descriptor(instance_id)
        resource = self._cache.get(
            descriptor.server_id,
            descriptor.resource_sha256,
        )
        if resource is None:
            raise MCPAppChannelError(
                "resource_unavailable", "MCP App frame resource is unavailable"
            )
        return resource

    def accept_message(
        self,
        instance_id: str,
        *,
        source_id: str,
        raw_message: bytes,
    ) -> MCPAppBridgeRequest:
        """Validate, bind, and synchronously complete one allowed request."""
        bridge = self._sessions.get_bridge(instance_id)
        if bridge is None:
            raise MCPAppChannelError("frame_not_found", "MCP App frame not found")
        request = bridge.accept(source_id=source_id, raw_message=raw_message)
        if request.method == "ui/userChoice":
            choice_id = request.params["choiceId"]
            assert isinstance(choice_id, str)
            with self._lock:
                self._choices[instance_id] = choice_id
        bridge.complete(request.request_id)
        return request

    def choice(self, instance_id: str) -> str | None:
        """Return the latest bounded typed choice for downstream handling."""
        with self._lock:
            return self._choices.get(instance_id)

    def teardown(self, instance_id: str) -> tuple[str | int, ...]:
        """Destroy one frame and discard its ephemeral choice state."""
        cancelled = self._sessions.teardown(instance_id)
        if cancelled is None:
            raise MCPAppChannelError("frame_not_found", "MCP App frame not found")
        with self._lock:
            self._choices.pop(instance_id, None)
        return cancelled
