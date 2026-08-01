"""Revision-bound credential lease projections for the operator Web surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from gigaloom.contracts.credentials import (
    CredentialLeaseDecisionStatus,
    CredentialLeaseRequestV1,
)
from gigaloom.contracts.credentials_codec import (
    credential_revocation_receipt_to_dict,
)
from gigaloom.runtime.credentials import (
    CredentialQuotaMetadata,
    CredentialQuotaStatus,
    CredentialSourceRegistration,
    CredentialSourceScope,
    InMemoryCredentialBroker,
    credential_action_scope_digest,
    credential_source_projection_to_dict,
)
from gigaloom.secrets import SecretReference, SecretReferenceKind


DEMO_SOURCE_ID = "fake-github-demo"
DEMO_PARENT_RUN_ID = "credential-demo-run"
MAX_OPERATOR_LEASE_TTL_SECONDS = 300
_POLICY_DIGEST = hashlib.sha256(b"credential-operator-demo-policy-v1").hexdigest()


@dataclass(frozen=True, slots=True)
class CredentialOperatorSnapshot:
    """Bounded content-free snapshot returned to one operator."""

    revision: str
    broker_id: str
    sources: tuple[dict[str, Any], ...]
    leases: tuple[dict[str, Any], ...]
    content_free: Literal[True] = True


@dataclass(frozen=True, slots=True)
class CredentialOperatorAction:
    """Typed result for a revision-bound request or revocation."""

    state: Literal[
        "admitted",
        "denied",
        "stale",
        "revoked",
        "already_terminal",
    ]
    reason_code: str
    snapshot: CredentialOperatorSnapshot
    receipt: dict[str, Any] | None = None


class CredentialOperatorService:
    """Expose safe broker metadata and exact operator-owned lease actions."""

    def __init__(
        self,
        broker: InMemoryCredentialBroker,
        *,
        now: Callable[[], datetime] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(broker, InMemoryCredentialBroker):
            raise ValueError("credential operator broker is invalid")
        self._broker = broker
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._request_id_factory = request_id_factory or (
            lambda: f"credential-request-{uuid4().hex}"
        )

    @classmethod
    def with_fake_broker_demo(
        cls,
        broker: InMemoryCredentialBroker,
        *,
        now: Callable[[], datetime] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> CredentialOperatorService:
        """Create the release demo without registering secret material."""
        service = cls(
            broker,
            now=now,
            request_id_factory=request_id_factory,
        )
        broker.register_source(
            CredentialSourceRegistration(
                source_id=DEMO_SOURCE_ID,
                broker_id=broker.broker_id,
                provider_identity_ref="fake-github-provider@demo-v1",
                secret_reference=SecretReference(
                    SecretReferenceKind.TEST,
                    "GIGALOOM_FAKE_BROKER_DEMO",
                ),
                scope=CredentialSourceScope(
                    audiences=("api.example.test",),
                    resources=("gigaloom/demo",),
                    operation_classes=("issue.write",),
                ),
                quota=CredentialQuotaMetadata(
                    status=CredentialQuotaStatus.NOT_APPLICABLE,
                    reason_code="hermetic_demo",
                ),
                max_lease_ttl_seconds=MAX_OPERATOR_LEASE_TTL_SECONDS,
                max_active_leases=4,
            )
        )
        return service

    def snapshot(self) -> CredentialOperatorSnapshot:
        """Return current source and lease metadata under one revision."""
        now = self._current_time()
        sources = tuple(
            self._source_payload(item, now=now)
            for item in self._broker.list_source_projections()
        )
        leases = tuple(self._lease_payload(item) for item in self._broker.list_leases())
        semantic = {
            "broker_id": self._broker.broker_id,
            "sources": sources,
            "leases": leases,
        }
        return CredentialOperatorSnapshot(
            revision=_digest(semantic),
            broker_id=self._broker.broker_id,
            sources=sources,
            leases=leases,
        )

    def request_lease(
        self,
        *,
        expected_revision: str,
        source_id: str,
        audience: str,
        resource: str,
        operation_class: str,
        parent_run_id: str,
        ttl_seconds: int,
    ) -> CredentialOperatorAction:
        """Request one exact action scope only from the reviewed snapshot."""
        before = self.snapshot()
        if expected_revision != before.revision:
            return CredentialOperatorAction(
                state="stale",
                reason_code="credential_snapshot_changed",
                snapshot=before,
            )
        source = self._broker.source_projection(source_id)
        now = self._current_time()
        decision = self._broker.request_lease(
            CredentialLeaseRequestV1(
                request_id=self._request_id_factory(),
                secret_ref_id=source.secret_ref_id,
                broker_id=source.broker_id,
                audience=audience,
                resource=resource,
                operation_class=operation_class,
                requested_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                parent_run_id=parent_run_id,
                policy_digest=_POLICY_DIGEST,
                scope_digest=credential_action_scope_digest(
                    audience=audience,
                    resource=resource,
                    operation_class=operation_class,
                ),
            )
        )
        return CredentialOperatorAction(
            state=(
                "admitted"
                if decision.status is CredentialLeaseDecisionStatus.ADMITTED
                else "denied"
            ),
            reason_code=decision.reason_code,
            snapshot=self.snapshot(),
        )

    def revoke_lease(
        self,
        lease_id: str,
        *,
        expected_revision: str,
    ) -> CredentialOperatorAction:
        """Revoke one lease only from the exact reviewed snapshot."""
        before = self.snapshot()
        if expected_revision != before.revision:
            return CredentialOperatorAction(
                state="stale",
                reason_code="credential_snapshot_changed",
                snapshot=before,
            )
        receipt = self._broker.revoke_lease(
            lease_id,
            reason_code="operator_revoked",
            requested_at=self._current_time(),
        )
        return CredentialOperatorAction(
            state=receipt.status.value,
            reason_code=receipt.reason_code,
            snapshot=self.snapshot(),
            receipt=credential_revocation_receipt_to_dict(receipt),
        )

    def _current_time(self) -> datetime:
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("credential operator clock must return aware datetime")
        return now

    @staticmethod
    def _source_payload(source: Any, *, now: datetime) -> dict[str, Any]:
        projected = credential_source_projection_to_dict(source)
        expires_at = source.expires_at
        return {
            "source_id": projected["source_id"],
            "broker_id": projected["broker_id"],
            "provider_identity_ref": projected["provider_identity_ref"],
            "reference_kind": projected["secret_ref_kind"],
            "scope": projected["scope"],
            "quota": projected["quota"],
            "expires_at": projected["expires_at"],
            "state": "expired"
            if expires_at is not None and expires_at <= now
            else "current",
            "demo": projected["source_id"] == DEMO_SOURCE_ID,
        }

    @staticmethod
    def _lease_payload(lease: Any) -> dict[str, Any]:
        return {
            "lease_id": lease.lease_id,
            "broker_id": lease.broker_id,
            "audience": lease.audience,
            "resource": lease.resource,
            "operation_class": lease.operation_class,
            "issued_at": lease.issued_at.isoformat(),
            "expires_at": lease.expires_at.isoformat(),
            "parent_run_id": lease.parent_run_id,
            "scope_digest": lease.scope_digest,
            "status": lease.status.value,
        }


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CredentialOperatorAction",
    "CredentialOperatorService",
    "CredentialOperatorSnapshot",
    "DEMO_PARENT_RUN_ID",
    "DEMO_SOURCE_ID",
    "MAX_OPERATOR_LEASE_TTL_SECONDS",
]
