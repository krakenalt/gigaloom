"""Lifecycle owner for one generic ACP connection generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import threading
from typing import Any

from gigaloom.harnesses.acp.contracts import (
    AcpCapabilitySnapshotV1,
    AcpClientInfo,
    AcpLimits,
    AcpProcessSpec,
)
from gigaloom.harnesses.acp.errors import AcpLifecycleError, AcpProtocolError
from gigaloom.harnesses.acp.initialize import initialize_connection
from gigaloom.harnesses.acp.jsonrpc import create_acp_supervisor
from gigaloom.structured_processes import (
    NormalizedStructuredEvent,
    StructuredProcessSupervisor,
    StructuredTransport,
)


def _ignore_initialize_events(
    method: str, params: Mapping[str, Any]
) -> NormalizedStructuredEvent | None:
    del method, params
    return None


class AcpClient:
    """Start, initialize, and close one local ACP process generation."""

    def __init__(
        self,
        spec: AcpProcessSpec,
        *,
        compatibility_profile_digest: str,
        limits: AcpLimits,
        client_info: AcpClientInfo,
        supervisor: StructuredProcessSupervisor,
    ) -> None:
        self.spec = spec
        self.compatibility_profile_digest = compatibility_profile_digest
        self.limits = limits
        self.client_info = client_info
        self.supervisor = supervisor
        self._snapshot: AcpCapabilitySnapshotV1 | None = None
        self._lock = threading.RLock()

    @property
    def capability_snapshot(self) -> AcpCapabilitySnapshotV1 | None:
        """Return the snapshot for the current initialized generation, if any."""
        with self._lock:
            return self._snapshot

    def start(self) -> int:
        """Start the admitted process without implicitly initializing twice."""
        with self._lock:
            if self._snapshot is not None:
                raise AcpLifecycleError("ACP client is already initialized")
            return self.supervisor.start()

    def initialize(self) -> AcpCapabilitySnapshotV1:
        """Initialize exactly once for the active process generation."""
        with self._lock:
            if self._snapshot is not None:
                raise AcpLifecycleError(
                    "ACP connection generation is already initialized"
                )
            try:
                snapshot = initialize_connection(
                    self.supervisor,
                    self.spec,
                    client_info=self.client_info,
                    compatibility_profile_digest=self.compatibility_profile_digest,
                    timeout=self.limits.request_timeout_seconds,
                )
            except AcpProtocolError:
                self.supervisor.close()
                raise
            self._snapshot = snapshot
            return snapshot

    def close(self) -> None:
        """Close the process and invalidate the generation snapshot."""
        with self._lock:
            self.supervisor.close()
            self._snapshot = None


def create_acp_client(
    spec: AcpProcessSpec,
    *,
    compatibility_profile_digest: str,
    limits: AcpLimits | None = None,
    client_info: AcpClientInfo | None = None,
    event_normalizer: Callable[
        [str, Mapping[str, Any]], NormalizedStructuredEvent | None
    ] = _ignore_initialize_events,
    transport_factory: Callable[[], StructuredTransport] | None = None,
) -> AcpClient:
    """Create an unstarted client with explicit injectable transport ownership."""
    resolved_limits = limits or AcpLimits()
    return AcpClient(
        spec,
        compatibility_profile_digest=compatibility_profile_digest,
        limits=resolved_limits,
        client_info=client_info or AcpClientInfo(),
        supervisor=create_acp_supervisor(
            spec,
            limits=resolved_limits,
            event_normalizer=event_normalizer,
            transport_factory=transport_factory,
        ),
    )
