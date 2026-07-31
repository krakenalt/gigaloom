"""Bounded in-memory cache for admitted MCP App HTML resources."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .contracts import AdmittedMCPAppResource, MCPAppLimits
from .errors import MCPAppCacheError


@dataclass(frozen=True, slots=True)
class MCPAppCacheSnapshot:
    """Content-free cache accounting for diagnostics and tests."""

    resources: int
    total_bytes: int
    resources_by_server: dict[str, int]


class MCPAppResourceCache:
    """Reject-on-limit cache with server and workspace-wide ceilings."""

    def __init__(self, *, limits: MCPAppLimits | None = None) -> None:
        self._limits = limits or MCPAppLimits()
        self._resources: dict[tuple[str, str], AdmittedMCPAppResource] = {}
        self._bytes = 0
        self._lock = RLock()

    def put(self, resource: AdmittedMCPAppResource) -> None:
        """Cache an admitted immutable resource without implicit eviction."""
        with self._lock:
            key = (resource.server_id, resource.sha256)
            current = self._resources.get(key)
            if current is not None:
                if current != resource:
                    raise MCPAppCacheError(
                        "digest_collision",
                        "Cached MCP App digest resolves to different metadata",
                    )
                return
            server_count = sum(
                1
                for server_id, _digest in self._resources
                if server_id == resource.server_id
            )
            if server_count >= self._limits.max_cached_resources_per_server:
                raise MCPAppCacheError(
                    "server_cache_limit",
                    "MCP App server resource cache limit reached",
                )
            if (
                self._bytes + resource.size_bytes
                > self._limits.max_workspace_cache_bytes
            ):
                raise MCPAppCacheError(
                    "workspace_cache_limit",
                    "MCP App workspace cache byte limit reached",
                )
            self._resources[key] = resource
            self._bytes += resource.size_bytes

    def get(self, server_id: str, digest: str) -> AdmittedMCPAppResource | None:
        """Resolve a resource only by its server-bound digest."""
        with self._lock:
            return self._resources.get((server_id, digest))

    def snapshot(self) -> MCPAppCacheSnapshot:
        """Return bounded accounting without resource content."""
        with self._lock:
            counts: dict[str, int] = {}
            for server_id, _digest in self._resources:
                counts[server_id] = counts.get(server_id, 0) + 1
            return MCPAppCacheSnapshot(
                resources=len(self._resources),
                total_bytes=self._bytes,
                resources_by_server=counts,
            )
