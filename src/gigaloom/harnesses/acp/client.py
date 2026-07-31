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
    AcpRouteIdentity,
)
from gigaloom.harnesses.acp.errors import AcpLifecycleError, AcpProtocolError
from gigaloom.harnesses.acp.initialize import initialize_connection
from gigaloom.harnesses.acp.jsonrpc import create_acp_supervisor
from gigaloom.harnesses.acp.updates import normalize_acp_update
from gigaloom.structured_processes import (
    NormalizedStructuredEvent,
    StructuredProcessSupervisor,
    StructuredTransport,
)


class AcpClient:
    """Start, initialize, and close one local ACP process generation."""

    def __init__(
        self,
        spec: AcpProcessSpec,
        *,
        compatibility_profile_digest: str,
        route_identity: AcpRouteIdentity,
        limits: AcpLimits,
        client_info: AcpClientInfo,
        supervisor: StructuredProcessSupervisor,
    ) -> None:
        self.spec = spec
        self.compatibility_profile_digest = compatibility_profile_digest
        if compatibility_profile_digest != route_identity.profile_digest:
            raise ValueError("ACP compatibility and route profile digests must match")
        self.route_identity = route_identity
        self.limits = limits
        self.client_info = client_info
        self.supervisor = supervisor
        self._snapshot: AcpCapabilitySnapshotV1 | None = None
        self._sessions: dict[str, Any] = {}
        self._session_config_types: dict[str, dict[str, str]] = {}
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
            self._sessions.clear()
            self._session_config_types.clear()

    def _register_session(self, binding: Any, config_types: Mapping[str, str]) -> None:
        with self._lock:
            if binding.acp_session_id in self._sessions:
                raise AcpLifecycleError("ACP session is already bound")
            self._sessions[binding.acp_session_id] = binding
            self._session_config_types[binding.acp_session_id] = dict(config_types)

    def _session(self, session_id: str) -> Any | None:
        with self._lock:
            return self._sessions.get(session_id)

    def _drop_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._session_config_types.pop(session_id, None)

    def _session_config_type(self, session_id: str, config_id: str) -> str:
        with self._lock:
            try:
                return self._session_config_types[session_id][config_id]
            except KeyError as exc:
                raise AcpProtocolError(
                    "ACP session config option was not offered"
                ) from exc


def create_acp_client(
    spec: AcpProcessSpec,
    *,
    compatibility_profile_digest: str,
    route_identity: AcpRouteIdentity,
    limits: AcpLimits | None = None,
    client_info: AcpClientInfo | None = None,
    event_normalizer: Callable[
        [str, Mapping[str, Any]], NormalizedStructuredEvent | None
    ] = normalize_acp_update,
    transport_factory: Callable[[], StructuredTransport] | None = None,
) -> AcpClient:
    """Create an unstarted client with explicit injectable transport ownership."""
    resolved_limits = limits or AcpLimits()
    return AcpClient(
        spec,
        compatibility_profile_digest=compatibility_profile_digest,
        route_identity=route_identity,
        limits=resolved_limits,
        client_info=client_info or AcpClientInfo(),
        supervisor=create_acp_supervisor(
            spec,
            limits=resolved_limits,
            event_normalizer=event_normalizer,
            transport_factory=transport_factory,
        ),
    )
