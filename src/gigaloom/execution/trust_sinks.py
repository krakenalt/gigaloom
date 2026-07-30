"""Protected sink adapters composed with existing authority owners."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar

from gigaloom.contracts import (
    DataFlowReceipt,
    SinkDecisionStatus,
    SinkKind,
    SinkRequest,
)
from gigaloom.execution.trust import SourceToSinkGuard


_T = TypeVar("_T")


class NetworkAuthorityTicket(Protocol):
    """Network owner fields checked before its dispatch validation."""

    url_sha256: str
    request_body_bytes: int
    request_body_sha256: str | None

    def validate_request_body(
        self,
        *,
        body_bytes: int,
        body_sha256: str | None,
        now: str,
    ) -> Mapping[str, Any]:
        """Validate exact body binding and ticket lifetime."""


class GitHubOperation(Protocol):
    """Operation enum surface needed from the GitHub authority owner."""

    value: str


class GitHubCapabilityIntent(Protocol):
    """GitHub write preview fields bound by its authority owner."""

    repository: str
    operation: GitHubOperation
    resource_id: str | None
    payload_bytes: int
    payload_sha256: str | None
    preview_sha256: str


class GitHubAuthorityTicket(Protocol):
    """GitHub owner dispatch validation used after source admission."""

    preview_sha256: str

    def validate_before_dispatch(
        self,
        request: GitHubCapabilityIntent,
        *,
        current_credential_binding_sha256: str,
        now: str,
        retry: bool = False,
    ) -> Mapping[str, Any]:
        """Revalidate the exact GitHub preview and current credential."""


class ProtectedSinkDenied(PermissionError):
    """Content-free denial raised before an external side effect."""

    def __init__(
        self,
        reason: str,
        *,
        receipt: DataFlowReceipt | None = None,
    ) -> None:
        self.reason = reason
        self.receipt = receipt
        super().__init__(reason)


@dataclass(frozen=True)
class GuardedSinkResult(Generic[_T]):
    """Side-effect result plus both required bounded receipts."""

    result: _T
    data_flow_receipt: DataFlowReceipt
    authority_receipt: Mapping[str, Any]


def dispatch_guarded_network_sink(
    request: SinkRequest,
    *,
    payload: str | bytes,
    ticket: NetworkAuthorityTicket,
    now: str,
    dispatch: Callable[[bytes], _T],
    guard: SourceToSinkGuard | None = None,
) -> GuardedSinkResult[_T]:
    """Dispatch one network body only after trust and network owner validation."""
    receipt = _admitted_receipt(
        request,
        expected_kind=SinkKind.NETWORK_URL,
        guard=guard,
    )
    body = _payload_bytes(payload)
    body_sha256 = hashlib.sha256(body).hexdigest() if body else None
    _require_binding(
        request.payload_sha256 == hashlib.sha256(body).hexdigest(),
        "network_payload_changed_after_source_review",
        receipt,
    )
    _require_binding(
        request.destination_sha256 == ticket.url_sha256,
        "network_destination_changed_after_source_review",
        receipt,
    )
    _require_binding(
        len(body) == ticket.request_body_bytes
        and body_sha256 == ticket.request_body_sha256,
        "network_body_changed_after_authority_review",
        receipt,
    )
    authority_receipt = ticket.validate_request_body(
        body_bytes=len(body),
        body_sha256=body_sha256,
        now=now,
    )
    return GuardedSinkResult(
        result=dispatch(body),
        data_flow_receipt=receipt,
        authority_receipt=_receipt_mapping(authority_receipt),
    )


def dispatch_guarded_github_issue_write(
    request: SinkRequest,
    *,
    payload: str | bytes,
    capability: GitHubCapabilityIntent,
    ticket: GitHubAuthorityTicket,
    current_credential_binding_sha256: str,
    now: str,
    dispatch: Callable[[bytes], _T],
    retry: bool = False,
    guard: SourceToSinkGuard | None = None,
) -> GuardedSinkResult[_T]:
    """Dispatch one exact GitHub issue write after both admission boundaries."""
    receipt = _admitted_receipt(
        request,
        expected_kind=SinkKind.EXTERNAL_WRITE,
        guard=guard,
    )
    body = _payload_bytes(payload)
    body_sha256 = hashlib.sha256(body).hexdigest()
    _require_binding(
        request.payload_sha256 == body_sha256,
        "github_payload_changed_after_source_review",
        receipt,
    )
    _require_binding(
        getattr(capability.operation, "value", capability.operation) == "issue.write",
        "github_operation_is_outside_bounded_slice",
        receipt,
    )
    resource_id = str(capability.resource_id or "")
    _require_binding(
        resource_id.isdigit() and int(resource_id) > 0,
        "github_issue_identity_is_invalid",
        receipt,
    )
    destination = f"github://{capability.repository.lower()}/issues/{int(resource_id)}"
    destination_sha256 = hashlib.sha256(destination.encode("utf-8")).hexdigest()
    _require_binding(
        request.destination_metadata == destination
        and request.destination_sha256 == destination_sha256,
        "github_destination_changed_after_source_review",
        receipt,
    )
    _require_binding(
        capability.payload_bytes == len(body)
        and capability.payload_sha256 == body_sha256,
        "github_payload_changed_after_authority_review",
        receipt,
    )
    _require_binding(
        ticket.preview_sha256 == capability.preview_sha256,
        "github_preview_changed_before_dispatch",
        receipt,
    )
    authority_receipt = ticket.validate_before_dispatch(
        capability,
        current_credential_binding_sha256=current_credential_binding_sha256,
        now=now,
        retry=retry,
    )
    return GuardedSinkResult(
        result=dispatch(body),
        data_flow_receipt=receipt,
        authority_receipt=_receipt_mapping(authority_receipt),
    )


def _admitted_receipt(
    request: SinkRequest,
    *,
    expected_kind: SinkKind,
    guard: SourceToSinkGuard | None,
) -> DataFlowReceipt:
    if request.sink_kind is not expected_kind:
        raise ProtectedSinkDenied("protected_sink_kind_mismatch")
    decision, receipt = (guard or SourceToSinkGuard()).admit(request)
    if decision.status is not SinkDecisionStatus.ALLOW:
        raise ProtectedSinkDenied(
            f"source_to_sink.{decision.reason.value}",
            receipt=receipt,
        )
    return receipt


def _require_binding(
    condition: bool,
    reason: str,
    receipt: DataFlowReceipt,
) -> None:
    if not condition:
        raise ProtectedSinkDenied(reason, receipt=receipt)


def _payload_bytes(payload: str | bytes) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    raise ProtectedSinkDenied("protected_sink_payload_is_invalid")


def _receipt_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtectedSinkDenied("authority_receipt_is_invalid")
    return dict(value)


__all__ = [
    "GuardedSinkResult",
    "ProtectedSinkDenied",
    "dispatch_guarded_github_issue_write",
    "dispatch_guarded_network_sink",
]
