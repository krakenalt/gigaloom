"""In-memory credential broker used for hermetic control-plane execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from threading import RLock
from uuid import uuid4

from gigaloom.contracts.credentials import (
    CredentialLeaseDecisionStatus,
    CredentialLeaseDecisionV1,
    CredentialLeaseRequestV1,
    CredentialLeaseStatus,
    CredentialLeaseV1,
    CredentialRevocationReceiptV1,
    CredentialRevocationStatus,
)
from gigaloom.contracts.operational_validation import validate_identity

from gigaloom.runtime.credentials.models import (
    CredentialSourceProjection,
    CredentialSourceRegistration,
    credential_action_scope_digest,
    project_credential_source,
)


class CredentialSourceConflictError(ValueError):
    """Raised when one source id is rebound to different metadata."""


class CredentialSourceNotFoundError(LookupError):
    """Raised when one unknown source projection is requested."""


class CredentialLeaseNotFoundError(LookupError):
    """Raised when one unknown credential lease is requested."""


class CredentialLeaseDeniedError(PermissionError):
    """Content-free active-lease denial for the final egress boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class _LeaseRecord:
    source_id: str
    lease: CredentialLeaseV1


class InMemoryCredentialBroker:
    """Fake broker with atomic metadata-only lease admission and revocation."""

    def __init__(
        self,
        broker_id: str,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        if not isinstance(broker_id, str) or not broker_id:
            raise ValueError("credential broker id is invalid")
        self._broker_id = broker_id
        self._sources: dict[str, CredentialSourceRegistration] = {}
        self._leases: dict[str, _LeaseRecord] = {}
        self._terminal_receipts: dict[str, CredentialRevocationReceiptV1] = {}
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        self._available = True
        self._lock = RLock()

    @property
    def broker_id(self) -> str:
        """Return the immutable broker identity."""
        return self._broker_id

    def register_source(
        self,
        source: CredentialSourceRegistration,
    ) -> CredentialSourceProjection:
        """Register one exact SecretRef owner and return safe metadata."""
        if not isinstance(source, CredentialSourceRegistration):
            raise ValueError("credential source registration is invalid")
        if source.broker_id != self._broker_id:
            raise ValueError("credential source broker binding mismatch")
        with self._lock:
            previous = self._sources.get(source.source_id)
            if previous is not None and previous != source:
                raise CredentialSourceConflictError(
                    "credential source registration conflicts"
                )
            self._sources[source.source_id] = source
        return project_credential_source(source)

    def source_projection(self, source_id: str) -> CredentialSourceProjection:
        """Return one content-free source projection."""
        with self._lock:
            source = self._sources.get(source_id)
        if source is None:
            raise CredentialSourceNotFoundError("credential source not found")
        return project_credential_source(source)

    def list_source_projections(self) -> tuple[CredentialSourceProjection, ...]:
        """Return stable projections ordered by source id."""
        with self._lock:
            sources = tuple(self._sources[key] for key in sorted(self._sources))
        return tuple(project_credential_source(source) for source in sources)

    def set_available(self, available: bool) -> None:
        """Set hermetic broker availability without affecting local work."""
        if not isinstance(available, bool):
            raise ValueError("credential broker availability must be boolean")
        with self._lock:
            self._available = available

    def request_lease(
        self,
        request: CredentialLeaseRequestV1,
    ) -> CredentialLeaseDecisionV1:
        """Atomically admit one exact, time-bounded action lease."""
        if not isinstance(request, CredentialLeaseRequestV1):
            raise ValueError("credential lease request is invalid")
        now = self._current_time()
        with self._lock:
            self._expire_unlocked(now)
            reason = self._admission_denial_reason(request, now=now)
            if reason is not None:
                return self._decision(request, now=now, reason_code=reason)
            source = self._matching_source(request)
            assert source is not None
            lease = CredentialLeaseV1(
                lease_id=self._new_id("credential-lease"),
                secret_ref_id=request.secret_ref_id,
                broker_id=request.broker_id,
                audience=request.audience,
                resource=request.resource,
                operation_class=request.operation_class,
                issued_at=now,
                expires_at=request.expires_at,
                parent_run_id=request.parent_run_id,
                policy_digest=request.policy_digest,
                scope_digest=request.scope_digest,
                status=CredentialLeaseStatus.ACTIVE,
            )
            self._leases[lease.lease_id] = _LeaseRecord(source.source_id, lease)
            return self._decision(
                request,
                now=now,
                reason_code="admitted",
                lease=lease,
            )

    def lease(self, lease_id: str) -> CredentialLeaseV1:
        """Return current metadata, lazily applying expiry first."""
        validate_identity(lease_id, field_name="credential lease id")
        now = self._current_time()
        with self._lock:
            self._expire_unlocked(now)
            record = self._leases.get(lease_id)
            if record is None:
                raise CredentialLeaseNotFoundError("credential lease not found")
            return record.lease

    def active_lease_count(self, secret_ref_id: str) -> int:
        """Return active headroom use for one opaque SecretRef identity."""
        now = self._current_time()
        with self._lock:
            self._expire_unlocked(now)
            return sum(
                record.lease.status is CredentialLeaseStatus.ACTIVE
                and record.lease.secret_ref_id == secret_ref_id
                for record in self._leases.values()
            )

    def source_for_active_lease(
        self,
        lease: CredentialLeaseV1,
        *,
        current_policy_digest: str,
    ) -> CredentialSourceRegistration:
        """Revalidate one exact lease and return its private SecretRef owner."""
        if not isinstance(lease, CredentialLeaseV1):
            raise CredentialLeaseDeniedError("lease_invalid")
        now = self._current_time()
        with self._lock:
            self._expire_unlocked(now)
            if not self._available:
                raise CredentialLeaseDeniedError("broker_unavailable")
            record = self._leases.get(lease.lease_id)
            if record is None:
                raise CredentialLeaseDeniedError("lease_not_found")
            if record.lease != lease:
                raise CredentialLeaseDeniedError("lease_binding_changed")
            if record.lease.status is not CredentialLeaseStatus.ACTIVE:
                raise CredentialLeaseDeniedError("lease_inactive")
            if record.lease.policy_digest != current_policy_digest:
                raise CredentialLeaseDeniedError("lease_policy_changed")
            source = self._sources.get(record.source_id)
            if source is None:
                raise CredentialLeaseDeniedError("credential_source_missing")
            if source.secret_reference.identity != lease.secret_ref_id:
                raise CredentialLeaseDeniedError("secret_reference_changed")
            return source

    def revoke_lease(
        self,
        lease_id: str,
        *,
        reason_code: str,
        requested_at: datetime,
    ) -> CredentialRevocationReceiptV1:
        """Revoke one lease idempotently and return content-free evidence."""
        validate_identity(lease_id, field_name="credential lease id")
        validate_identity(reason_code, field_name="credential revocation reason")
        with self._lock:
            record = self._leases.get(lease_id)
            if record is None:
                raise CredentialLeaseNotFoundError("credential lease not found")
            if record.lease.status is CredentialLeaseStatus.ACTIVE:
                receipt = self._terminalize_unlocked(
                    record,
                    final_status=CredentialLeaseStatus.REVOKED,
                    reason_code=reason_code,
                    requested_at=requested_at,
                )
                assert receipt is not None
                return receipt
            return self._already_terminal_receipt(
                record.lease,
                reason_code=reason_code,
                requested_at=requested_at,
            )

    def revoke_run(
        self,
        parent_run_id: str,
        *,
        reason_code: str,
        requested_at: datetime,
    ) -> tuple[CredentialRevocationReceiptV1, ...]:
        """Revoke every active lease for one run exactly once."""
        validate_identity(parent_run_id, field_name="credential parent run id")
        validate_identity(reason_code, field_name="credential revocation reason")
        receipts: list[CredentialRevocationReceiptV1] = []
        with self._lock:
            for record in tuple(self._leases.values()):
                if (
                    record.lease.parent_run_id == parent_run_id
                    and record.lease.status is CredentialLeaseStatus.ACTIVE
                ):
                    receipt = self._terminalize_unlocked(
                        record,
                        final_status=CredentialLeaseStatus.REVOKED,
                        reason_code=reason_code,
                        requested_at=requested_at,
                    )
                    if receipt is not None:
                        receipts.append(receipt)
        return tuple(receipts)

    def expire_leases(self) -> tuple[CredentialRevocationReceiptV1, ...]:
        """Expire every elapsed lease and emit each terminal receipt once."""
        with self._lock:
            return self._expire_unlocked(self._current_time())

    def _admission_denial_reason(
        self,
        request: CredentialLeaseRequestV1,
        *,
        now: datetime,
    ) -> str | None:
        if not self._available:
            return "broker_unavailable"
        if request.broker_id != self._broker_id:
            return "broker_binding_mismatch"
        if request.requested_at > now:
            return "request_not_yet_valid"
        if request.expires_at <= now:
            return "request_expired"
        expected_scope = credential_action_scope_digest(
            audience=request.audience,
            resource=request.resource,
            operation_class=request.operation_class,
        )
        if request.scope_digest != expected_scope:
            return "scope_digest_mismatch"
        source = self._matching_source(request)
        if source is None:
            matching_reference = any(
                item.secret_reference.identity == request.secret_ref_id
                for item in self._sources.values()
            )
            return "source_scope_denied" if matching_reference else "source_not_found"
        if source.expires_at is not None and source.expires_at <= now:
            return "source_expired"
        if source.expires_at is not None and request.expires_at > source.expires_at:
            return "source_lifetime_exceeded"
        requested_ttl = (request.expires_at - request.requested_at).total_seconds()
        if requested_ttl > source.max_lease_ttl_seconds:
            return "lease_ttl_exceeded"
        parent_reason = self._parent_denial_reason(request)
        if parent_reason is not None:
            return parent_reason
        active = sum(
            record.source_id == source.source_id
            and record.lease.status is CredentialLeaseStatus.ACTIVE
            for record in self._leases.values()
        )
        if active >= source.max_active_leases:
            return "policy_headroom_exceeded"
        return None

    def _parent_denial_reason(self, request: CredentialLeaseRequestV1) -> str | None:
        if request.parent_lease_id is None:
            return None
        parent_record = self._leases.get(request.parent_lease_id)
        if parent_record is None:
            return "parent_lease_not_found"
        parent = parent_record.lease
        if parent.status is not CredentialLeaseStatus.ACTIVE:
            return "parent_lease_inactive"
        if parent.parent_run_id != request.parent_run_id:
            return "parent_run_mismatch"
        if parent.policy_digest != request.policy_digest:
            return "parent_policy_mismatch"
        if request.expires_at > parent.expires_at:
            return "parent_lifetime_exceeded"
        if (
            parent.secret_ref_id != request.secret_ref_id
            or parent.audience != request.audience
            or parent.resource != request.resource
            or parent.operation_class != request.operation_class
            or parent.scope_digest != request.scope_digest
        ):
            return "parent_scope_exceeded"
        return None

    def _matching_source(
        self,
        request: CredentialLeaseRequestV1,
    ) -> CredentialSourceRegistration | None:
        matches = tuple(
            source
            for source in self._sources.values()
            if source.secret_reference.identity == request.secret_ref_id
            and source.scope.admits(
                audience=request.audience,
                resource=request.resource,
                operation_class=request.operation_class,
            )
        )
        if len(matches) != 1:
            return None
        return matches[0]

    def _decision(
        self,
        request: CredentialLeaseRequestV1,
        *,
        now: datetime,
        reason_code: str,
        lease: CredentialLeaseV1 | None = None,
    ) -> CredentialLeaseDecisionV1:
        return CredentialLeaseDecisionV1(
            decision_id=self._new_id("credential-decision"),
            request_id=request.request_id,
            status=(
                CredentialLeaseDecisionStatus.ADMITTED
                if lease is not None
                else CredentialLeaseDecisionStatus.DENIED
            ),
            reason_code=reason_code,
            decided_at=now,
            policy_digest=request.policy_digest,
            lease=lease,
        )

    def _expire_unlocked(
        self,
        now: datetime,
    ) -> tuple[CredentialRevocationReceiptV1, ...]:
        receipts: list[CredentialRevocationReceiptV1] = []
        for record in tuple(self._leases.values()):
            if (
                record.lease.status is CredentialLeaseStatus.ACTIVE
                and record.lease.expires_at <= now
            ):
                receipt = self._terminalize_unlocked(
                    record,
                    final_status=CredentialLeaseStatus.EXPIRED,
                    reason_code="lease_expired",
                    requested_at=now,
                )
                if receipt is not None:
                    receipts.append(receipt)
        return tuple(receipts)

    def _terminalize_unlocked(
        self,
        record: _LeaseRecord,
        *,
        final_status: CredentialLeaseStatus,
        reason_code: str,
        requested_at: datetime,
    ) -> CredentialRevocationReceiptV1 | None:
        if record.lease.status is not CredentialLeaseStatus.ACTIVE:
            return None
        terminal = replace(record.lease, status=final_status)
        self._leases[terminal.lease_id] = _LeaseRecord(record.source_id, terminal)
        receipt = CredentialRevocationReceiptV1(
            receipt_id=self._new_id("credential-revocation"),
            lease_id=terminal.lease_id,
            status=CredentialRevocationStatus.REVOKED,
            reason_code=reason_code,
            revoked_at=requested_at,
            idempotency_key_digest=_digest(
                {
                    "lease_id": terminal.lease_id,
                    "terminal_status": final_status.value,
                    "reason_code": reason_code,
                }
            ),
        )
        self._terminal_receipts[terminal.lease_id] = receipt
        return receipt

    def _already_terminal_receipt(
        self,
        lease: CredentialLeaseV1,
        *,
        reason_code: str,
        requested_at: datetime,
    ) -> CredentialRevocationReceiptV1:
        return CredentialRevocationReceiptV1(
            receipt_id=self._new_id("credential-revocation"),
            lease_id=lease.lease_id,
            status=CredentialRevocationStatus.ALREADY_TERMINAL,
            reason_code=reason_code,
            revoked_at=requested_at,
            idempotency_key_digest=_digest(
                {
                    "lease_id": lease.lease_id,
                    "terminal_status": lease.status.value,
                    "reason_code": reason_code,
                }
            ),
        )

    def _current_time(self) -> datetime:
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("credential broker clock must return aware datetime")
        return now

    def _new_id(self, prefix: str) -> str:
        value = self._id_factory(prefix)
        validate_identity(value, field_name=f"{prefix} id")
        return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CredentialLeaseDeniedError",
    "CredentialLeaseNotFoundError",
    "CredentialSourceConflictError",
    "CredentialSourceNotFoundError",
    "InMemoryCredentialBroker",
]
