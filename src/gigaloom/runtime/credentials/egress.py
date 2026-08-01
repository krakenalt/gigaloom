"""Last-mile, owner-bound credential injection for one hermetic HTTPS action."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from gigaloom.contracts.credentials import (
    CredentialInjectionReceiptV1,
    CredentialInjectionStatus,
    CredentialLeaseV1,
)
from gigaloom.contracts.operational_validation import (
    validate_digest,
    validate_identity,
    validate_text,
)
from gigaloom.runtime.credentials.broker import (
    CredentialLeaseDeniedError,
    InMemoryCredentialBroker,
)
from gigaloom.runtime.credentials.models import (
    credential_action_scope_digest,
)
from gigaloom.secrets import (
    ResolvedSecret,
    SecretResolutionError,
    SecretResolver,
)


GITHUB_ISSUE_WRITE_OPERATION = "issue.write"
GITHUB_ISSUE_WRITE_METHOD = "POST"


class CredentialTransportCancelled(RuntimeError):
    """Signal cancellation at the hermetic last-mile transport."""


class CredentialEgressDenied(PermissionError):
    """Safe egress denial carrying only a content-free receipt."""

    def __init__(self, reason: str, receipt: CredentialInjectionReceiptV1) -> None:
        self.reason = reason
        self.receipt = receipt
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class GitHubLikeCredentialRequest:
    """One content-free GitHub-like write intent bound to an active lease."""

    lease: CredentialLeaseV1
    url: str
    method: str
    repository: str
    operation_class: str
    policy_digest: str
    payload_bytes: int
    payload_sha256: str
    redirect_url: str | None = None
    request_digest: str = field(init=False)
    destination_digest: str = field(init=False)
    operation_digest: str = field(init=False)
    host: str = field(init=False)
    path: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.lease, CredentialLeaseV1):
            raise ValueError("credential egress lease is invalid")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError("credential egress requires a credential-free HTTPS URL")
        method = self.method.strip().upper()
        validate_identity(method, field_name="credential egress method")
        validate_text(
            self.repository,
            field_name="credential egress repository",
            max_chars=256,
        )
        validate_identity(
            self.operation_class,
            field_name="credential egress operation",
        )
        validate_digest(self.policy_digest, field_name="credential egress policy")
        validate_digest(self.payload_sha256, field_name="credential egress payload")
        if (
            isinstance(self.payload_bytes, bool)
            or not isinstance(self.payload_bytes, int)
            or not 0 <= self.payload_bytes <= 1024 * 1024
        ):
            raise ValueError("credential egress payload size is invalid")
        if self.redirect_url is not None:
            validate_text(
                self.redirect_url,
                field_name="credential egress redirect",
                max_chars=4_096,
            )
        host = parsed.hostname.lower()
        path = parsed.path
        semantic = {
            "lease_id": self.lease.lease_id,
            "url": self.url,
            "method": method,
            "repository": self.repository,
            "operation_class": self.operation_class,
            "policy_digest": self.policy_digest,
            "payload_bytes": self.payload_bytes,
            "payload_sha256": self.payload_sha256,
            "redirect_url": self.redirect_url,
        }
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "request_digest", _digest(semantic))
        object.__setattr__(
            self,
            "destination_digest",
            _digest({"host": host, "repository": self.repository, "path": path}),
        )
        object.__setattr__(
            self,
            "operation_digest",
            _digest(
                {
                    "method": method,
                    "operation_class": self.operation_class,
                    "payload_sha256": self.payload_sha256,
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the runtime payload without credential material."""
        return {
            "lease_id": self.lease.lease_id,
            "url": self.url,
            "method": self.method,
            "repository": self.repository,
            "operation_class": self.operation_class,
            "policy_digest": self.policy_digest,
            "payload_bytes": self.payload_bytes,
            "payload_sha256": self.payload_sha256,
            "redirect_url": self.redirect_url,
            "request_digest": self.request_digest,
            "destination_digest": self.destination_digest,
            "operation_digest": self.operation_digest,
        }


@dataclass(frozen=True, slots=True)
class CredentialTransportResult:
    """Bounded transport result with no body or response headers."""

    status_code: int
    response_digest: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("credential transport status is invalid")
        validate_digest(
            self.response_digest,
            field_name="credential transport response",
        )


class CredentialedTransportPort(Protocol):
    """Exact last-mile transport that alone may reveal the credential."""

    def dispatch(
        self,
        request: GitHubLikeCredentialRequest,
        *,
        credential: ResolvedSecret,
        reveal_owner: str,
    ) -> CredentialTransportResult:
        """Perform one hermetic operation without retaining the secret."""


@dataclass(frozen=True, slots=True)
class CredentialEgressResult:
    """Successful operation evidence without response or credential content."""

    receipt: CredentialInjectionReceiptV1
    transport: CredentialTransportResult

    def to_dict(self) -> dict[str, Any]:
        """Serialize safe result metadata for Web, stores, and capsules."""
        return {
            "receipt_id": self.receipt.receipt_id,
            "lease_id": self.receipt.lease_id,
            "status": self.receipt.status.value,
            "reason_code": self.receipt.reason_code,
            "request_digest": self.receipt.request_digest,
            "destination_digest": self.receipt.destination_digest,
            "operation_digest": self.receipt.operation_digest,
            "content_free": self.receipt.content_free,
            "transport": {
                "status_code": self.transport.status_code,
                "response_digest": self.transport.response_digest,
            },
        }


class HermeticCredentialEgress:
    """Validate every binding before resolving at the last-mile transport."""

    def __init__(
        self,
        broker: InMemoryCredentialBroker,
        resolver: SecretResolver,
        transport: CredentialedTransportPort,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._broker = broker
        self._resolver = resolver
        self._transport = transport
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")

    def dispatch(
        self,
        request: GitHubLikeCredentialRequest,
    ) -> CredentialEgressResult:
        """Resolve and inject only after exact destination and lease checks."""
        reason = self._preflight_denial_reason(request)
        if reason is not None:
            self._deny(request, reason)
        try:
            source = self._broker.source_for_active_lease(
                request.lease,
                current_policy_digest=request.policy_digest,
            )
        except CredentialLeaseDeniedError as error:
            if error.reason == "broker_unavailable":
                self._revoke_after_failure(request, error.reason)
            self._deny(request, error.reason)
        if source.secret_reference.identity != request.lease.secret_ref_id:
            self._deny(request, "secret_reference_changed")
        if not self._resolver.supports(source.secret_reference.kind):
            self._deny(request, "secret_resolver_unavailable")
        reveal_owner = f"credential-egress:{request.lease.lease_id}"
        try:
            resolved = self._resolver.resolve(
                source.secret_reference,
                owner=reveal_owner,
            )
        except SecretResolutionError:
            self._revoke_after_failure(request, "secret_resolution_failed")
            self._deny(request, "secret_resolution_failed")
        try:
            transport_result = self._transport.dispatch(
                request,
                credential=resolved,
                reveal_owner=reveal_owner,
            )
        except CredentialTransportCancelled:
            self._revoke_after_failure(request, "operation_cancelled")
            self._deny(request, "operation_cancelled")
        except Exception:
            self._revoke_after_failure(request, "egress_transport_failed")
            self._deny(request, "egress_transport_failed")
        receipt = self._receipt(
            request,
            status=CredentialInjectionStatus.INJECTED,
            reason_code="injected",
        )
        return CredentialEgressResult(receipt=receipt, transport=transport_result)

    def _preflight_denial_reason(
        self,
        request: GitHubLikeCredentialRequest,
    ) -> str | None:
        if not isinstance(request, GitHubLikeCredentialRequest):
            return "request_invalid"
        if request.redirect_url is not None:
            return "redirect_ambiguous"
        if request.host != request.lease.audience:
            return "audience_mismatch"
        if request.method != GITHUB_ISSUE_WRITE_METHOD:
            return "method_mismatch"
        if request.operation_class != GITHUB_ISSUE_WRITE_OPERATION:
            return "operation_mismatch"
        if request.repository != request.lease.resource:
            return "resource_mismatch"
        if request.operation_class != request.lease.operation_class:
            return "lease_operation_mismatch"
        expected_path = f"/repos/{request.repository}/issues"
        if request.path != expected_path:
            return "destination_path_mismatch"
        if request.policy_digest != request.lease.policy_digest:
            return "policy_mismatch"
        expected_scope = credential_action_scope_digest(
            audience=request.host,
            resource=request.repository,
            operation_class=request.operation_class,
        )
        if request.lease.scope_digest != expected_scope:
            return "scope_digest_mismatch"
        return None

    def _revoke_after_failure(
        self,
        request: GitHubLikeCredentialRequest,
        reason_code: str,
    ) -> None:
        self._broker.revoke_lease(
            request.lease.lease_id,
            reason_code=reason_code,
            requested_at=self._current_time(),
        )

    def _deny(self, request: GitHubLikeCredentialRequest, reason: str) -> None:
        raise CredentialEgressDenied(
            reason,
            self._receipt(
                request,
                status=CredentialInjectionStatus.DENIED,
                reason_code=reason,
            ),
        ) from None

    def _receipt(
        self,
        request: GitHubLikeCredentialRequest,
        *,
        status: CredentialInjectionStatus,
        reason_code: str,
    ) -> CredentialInjectionReceiptV1:
        receipt_id = self._id_factory("credential-injection")
        validate_identity(receipt_id, field_name="credential injection receipt id")
        return CredentialInjectionReceiptV1(
            receipt_id=receipt_id,
            lease_id=request.lease.lease_id,
            request_digest=request.request_digest,
            destination_digest=request.destination_digest,
            operation_digest=request.operation_digest,
            status=status,
            reason_code=reason_code,
            injected_at=self._current_time(),
        )

    def _current_time(self) -> datetime:
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("credential egress clock must return aware datetime")
        return now


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CredentialEgressDenied",
    "CredentialEgressResult",
    "CredentialTransportCancelled",
    "CredentialTransportResult",
    "CredentialedTransportPort",
    "GitHubLikeCredentialRequest",
    "HermeticCredentialEgress",
]
