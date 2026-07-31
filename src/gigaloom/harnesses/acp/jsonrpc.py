"""ACP-specific construction of the bounded JSON-RPC supervisor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from gigaloom.harnesses.acp.contracts import AcpLimits, AcpProcessSpec
from gigaloom.harnesses.acp.process import AcpTransportFactory
from gigaloom.structured_processes import (
    NormalizedStructuredEvent,
    StructuredProcessSupervisor,
    StructuredTransport,
)


EventNormalizer = Callable[[str, Mapping[str, Any]], NormalizedStructuredEvent | None]


class AcpProcessSupervisor(StructuredProcessSupervisor):
    """ACP supervisor that treats every framing/protocol fault as terminal."""

    def _protocol_fault(self, generation: int, reason: str) -> None:
        super()._protocol_fault(generation, reason)
        self._mark_lost(generation, reason)


def create_acp_supervisor(
    spec: AcpProcessSpec,
    *,
    limits: AcpLimits,
    event_normalizer: EventNormalizer,
    transport_factory: Callable[[], StructuredTransport] | None = None,
) -> AcpProcessSupervisor:
    """Create one supervisor with every ACP queue and request bound explicit."""
    return AcpProcessSupervisor(
        transport_factory or AcpTransportFactory(spec, limits),
        event_normalizer=event_normalizer,
        approval_methods=frozenset({"session/request_permission"}),
        event_queue_size=limits.max_inbound_messages,
        bridge_queue_size=limits.max_inbound_messages,
        max_frame_bytes=limits.max_message_bytes,
        stop_timeout_seconds=limits.shutdown_timeout_seconds,
        max_pending_requests=limits.max_outstanding_requests,
    )
