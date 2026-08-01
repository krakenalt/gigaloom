"""Secret-free credential lease contracts and public broker ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    validate_digest,
    validate_identity,
    validate_schema_version,
    validate_text,
    validate_time_range,
    validate_timestamp,
)


CREDENTIAL_CONTRACT_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION


class CredentialLeaseStatus(str, Enum):
    """Lifecycle state of one metadata-only credential lease."""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CredentialLeaseDecisionStatus(str, Enum):
    """Admission result for one lease request."""

    ADMITTED = "admitted"
    DENIED = "denied"


class CredentialInjectionStatus(str, Enum):
    """Result of a last-mile credential injection attempt."""

    INJECTED = "injected"
    DENIED = "denied"


class CredentialRevocationStatus(str, Enum):
    """Idempotent revocation result."""

    REVOKED = "revoked"
    ALREADY_TERMINAL = "already_terminal"


@dataclass(frozen=True, slots=True)
class CredentialLeaseRequestV1:
    """Request exact secret reference use without carrying secret material."""

    request_id: str
    secret_ref_id: str
    broker_id: str
    audience: str
    resource: str
    operation_class: str
    requested_at: datetime
    expires_at: datetime
    parent_run_id: str
    policy_digest: str
    scope_digest: str
    parent_lease_id: str | None = None
    schema_version: int = CREDENTIAL_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="credential request")
        for value, label in (
            (self.request_id, "credential request id"),
            (self.secret_ref_id, "secret ref id"),
            (self.broker_id, "credential broker id"),
            (self.audience, "credential audience"),
            (self.operation_class, "credential operation class"),
            (self.parent_run_id, "credential parent run id"),
        ):
            validate_identity(value, field_name=label)
        validate_text(self.resource, field_name="credential resource", max_chars=2_048)
        validate_time_range(
            self.requested_at,
            self.expires_at,
            field_name="credential request",
            allow_equal=False,
        )
        validate_digest(self.policy_digest, field_name="credential policy digest")
        validate_digest(self.scope_digest, field_name="credential scope digest")
        if self.parent_lease_id is not None:
            validate_identity(
                self.parent_lease_id,
                field_name="credential parent lease id",
            )


@dataclass(frozen=True, slots=True)
class CredentialLeaseV1:
    """Persistable credential metadata that never includes a secret value."""

    lease_id: str
    secret_ref_id: str
    broker_id: str
    audience: str
    resource: str
    operation_class: str
    issued_at: datetime
    expires_at: datetime
    parent_run_id: str
    policy_digest: str
    scope_digest: str
    status: CredentialLeaseStatus
    schema_version: int = CREDENTIAL_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="credential lease")
        for value, label in (
            (self.lease_id, "credential lease id"),
            (self.secret_ref_id, "secret ref id"),
            (self.broker_id, "credential broker id"),
            (self.audience, "credential audience"),
            (self.operation_class, "credential operation class"),
            (self.parent_run_id, "credential parent run id"),
        ):
            validate_identity(value, field_name=label)
        validate_text(self.resource, field_name="credential resource", max_chars=2_048)
        validate_time_range(
            self.issued_at,
            self.expires_at,
            field_name="credential lease",
            allow_equal=False,
        )
        validate_digest(self.policy_digest, field_name="credential policy digest")
        validate_digest(self.scope_digest, field_name="credential scope digest")
        if not isinstance(self.status, CredentialLeaseStatus):
            raise ValueError("credential lease status is invalid")


@dataclass(frozen=True, slots=True)
class CredentialLeaseDecisionV1:
    """Deterministic lease admission with an optional admitted lease."""

    decision_id: str
    request_id: str
    status: CredentialLeaseDecisionStatus
    reason_code: str
    decided_at: datetime
    policy_digest: str
    lease: CredentialLeaseV1 | None = None
    schema_version: int = CREDENTIAL_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="credential decision")
        validate_identity(self.decision_id, field_name="credential decision id")
        validate_identity(self.request_id, field_name="credential request id")
        validate_identity(self.reason_code, field_name="credential decision reason")
        validate_timestamp(self.decided_at, field_name="credential decision time")
        validate_digest(self.policy_digest, field_name="credential policy digest")
        if not isinstance(self.status, CredentialLeaseDecisionStatus):
            raise ValueError("credential decision status is invalid")
        if self.status is CredentialLeaseDecisionStatus.ADMITTED:
            if not isinstance(self.lease, CredentialLeaseV1):
                raise ValueError("admitted credential decision requires a lease")
            if self.lease.status is not CredentialLeaseStatus.ACTIVE:
                raise ValueError(
                    "admitted credential decision requires an active lease"
                )
            if self.lease.policy_digest != self.policy_digest:
                raise ValueError("credential decision policy digest mismatch")
        elif self.lease is not None:
            raise ValueError("denied credential decision cannot contain a lease")


@dataclass(frozen=True, slots=True)
class CredentialInjectionReceiptV1:
    """Content-free proof that last-mile injection was admitted or refused."""

    receipt_id: str
    lease_id: str
    request_digest: str
    destination_digest: str
    operation_digest: str
    status: CredentialInjectionStatus
    reason_code: str
    injected_at: datetime
    content_free: bool = True
    schema_version: int = CREDENTIAL_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="injection receipt")
        validate_identity(self.receipt_id, field_name="injection receipt id")
        validate_identity(self.lease_id, field_name="credential lease id")
        for value, label in (
            (self.request_digest, "injection request digest"),
            (self.destination_digest, "injection destination digest"),
            (self.operation_digest, "injection operation digest"),
        ):
            validate_digest(value, field_name=label)
        if not isinstance(self.status, CredentialInjectionStatus):
            raise ValueError("credential injection status is invalid")
        validate_identity(self.reason_code, field_name="injection reason code")
        validate_timestamp(self.injected_at, field_name="injection time")
        if self.content_free is not True:
            raise ValueError("credential injection receipts must be content-free")


@dataclass(frozen=True, slots=True)
class CredentialRevocationReceiptV1:
    """Idempotent content-free revocation evidence."""

    receipt_id: str
    lease_id: str
    status: CredentialRevocationStatus
    reason_code: str
    revoked_at: datetime
    idempotency_key_digest: str
    content_free: bool = True
    schema_version: int = CREDENTIAL_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="revocation receipt")
        validate_identity(self.receipt_id, field_name="revocation receipt id")
        validate_identity(self.lease_id, field_name="credential lease id")
        if not isinstance(self.status, CredentialRevocationStatus):
            raise ValueError("credential revocation status is invalid")
        validate_identity(self.reason_code, field_name="revocation reason code")
        validate_timestamp(self.revoked_at, field_name="revocation time")
        validate_digest(
            self.idempotency_key_digest,
            field_name="revocation idempotency key digest",
        )
        if self.content_free is not True:
            raise ValueError("credential revocation receipts must be content-free")


@runtime_checkable
class CredentialLeaseBrokerPort(Protocol):
    """Public metadata-only port implemented by credential brokers."""

    def request_lease(
        self,
        request: CredentialLeaseRequestV1,
    ) -> CredentialLeaseDecisionV1:
        """Evaluate one exact lease request."""

    def revoke_lease(
        self,
        lease_id: str,
        *,
        reason_code: str,
        requested_at: datetime,
    ) -> CredentialRevocationReceiptV1:
        """Revoke one lease idempotently."""


@runtime_checkable
class CredentialEgressPort(Protocol):
    """Public last-mile injection boundary with no serialized secret output."""

    def inject(
        self,
        lease: CredentialLeaseV1,
        *,
        request_digest: str,
        destination_digest: str,
        operation_digest: str,
    ) -> CredentialInjectionReceiptV1:
        """Inject only after exact egress admission and return metadata evidence."""
