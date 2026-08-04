"""Registry composition and capability refresh for standalone workers."""

from __future__ import annotations

import os
import socket
import time
from typing import Any

from gigaloom.config import HarnessConfig
from gigaloom.harnesses.api import (
    AgentIdentityInventory,
    acp_harnesses,
    create_agent_runtime_service,
)
from gigaloom.registry import HarnessRegistry, create_default_registry
from gigaloom.runtime.fingerprint import build_worker_fingerprint


class WorkerRegistryBinding:
    """Keep a worker registry and its advertised capabilities in sync."""

    def __init__(
        self,
        config: HarnessConfig,
        *,
        registry: HarnessRegistry | None,
        refresh_seconds: float,
    ) -> None:
        self.agent_runtime: Any | None = None
        if registry is None:
            registry, self.agent_runtime = _create_worker_registry(config)
        self.registry = registry
        self.registered = False
        self.refresh_seconds = max(refresh_seconds, 0.0)
        self.fingerprint = build_worker_fingerprint(registry)
        self._revision = _registry_revision(registry)
        self._next_refresh_at = time.monotonic() + self.refresh_seconds

    def advertise(self, runtime_store: Any, *, worker_id: str) -> dict[str, Any]:
        """Register current capabilities once and whenever their revision changes."""
        changed = self.refresh()
        if not self.registered or changed:
            runtime_store.register_worker(
                worker_id=worker_id,
                process_id=os.getpid(),
                hostname=socket.gethostname(),
                capability_fingerprint=self.fingerprint,
            )
            self.registered = True
        return self.fingerprint

    def refresh(self) -> bool:
        """Refresh the fingerprint after a managed ACP activation changes."""
        now = time.monotonic()
        if now < self._next_refresh_at:
            return False
        self._next_refresh_at = now + self.refresh_seconds
        revision = _registry_revision(self.registry)
        if revision == self._revision:
            return False
        self.fingerprint = build_worker_fingerprint(self.registry)
        self._revision = revision
        return True


def _create_worker_registry(
    config: HarnessConfig,
) -> tuple[HarnessRegistry, Any]:
    """Compose the standalone worker with the same managed ACP routes as the UI."""
    registry = create_default_registry()
    runtime = create_agent_runtime_service(
        config.data_dir,
        reserved_inventory=AgentIdentityInventory(local_agent_ids=registry.ids()),
    )
    registry.bind_dynamic_provider(lambda: acp_harnesses(runtime))
    return registry, runtime


def _registry_revision(registry: HarnessRegistry) -> tuple[tuple[str, str, str], ...]:
    """Return a cheap identity for built-in and active managed harness revisions."""
    revisions = []
    for harness in registry.list():
        spec = harness.spec()
        metadata = spec.metadata
        revisions.append(
            (
                spec.id,
                str(metadata.get("version") or ""),
                str(metadata.get("profile_digest") or ""),
            )
        )
    return tuple(sorted(revisions))
