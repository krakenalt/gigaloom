"""Bounded HTTP schemas for the credential operator surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Digest = str
Identity = str


class CredentialScopeResponse(BaseModel):
    audiences: list[str] = Field(max_length=64)
    resources: list[str] = Field(max_length=64)
    operation_classes: list[str] = Field(max_length=64)
    scope_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")


class CredentialQuotaResponse(BaseModel):
    status: Literal["known", "unknown", "not_applicable"]
    unit: str | None
    limit: int | None = Field(default=None, ge=0)
    remaining: int | None = Field(default=None, ge=0)
    resets_at: str | None
    reason_code: str | None


class CredentialSourceResponse(BaseModel):
    source_id: Identity
    broker_id: Identity
    provider_identity_ref: Identity
    reference_kind: Literal["environment", "keychain", "test"]
    scope: CredentialScopeResponse
    quota: CredentialQuotaResponse
    expires_at: str | None
    state: Literal["current", "expired"]
    demo: bool


class CredentialLeaseResponse(BaseModel):
    lease_id: Identity
    broker_id: Identity
    audience: Identity
    resource: str
    operation_class: Identity
    issued_at: str
    expires_at: str
    parent_run_id: Identity
    scope_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["active", "revoked", "expired"]


class CredentialOperatorSnapshotResponse(BaseModel):
    schema_version: Literal[1] = 1
    revision: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    broker_id: Identity
    sources: list[CredentialSourceResponse] = Field(max_length=64)
    leases: list[CredentialLeaseResponse] = Field(max_length=256)
    content_free: Literal[True]


class CredentialLeaseRequest(BaseModel):
    expected_revision: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: Identity = Field(min_length=1, max_length=256)
    audience: Identity = Field(min_length=1, max_length=256)
    resource: str = Field(min_length=1, max_length=2_048)
    operation_class: Identity = Field(min_length=1, max_length=256)
    parent_run_id: Identity = Field(min_length=1, max_length=256)
    ttl_seconds: int = Field(ge=1, le=300)


class CredentialRevokeRequest(BaseModel):
    expected_revision: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class CredentialRevocationReceiptResponse(BaseModel):
    receipt_id: Identity
    lease_id: Identity
    status: Literal["revoked", "already_terminal"]
    reason_code: Identity
    revoked_at: str
    idempotency_key_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    content_free: Literal[True]


class CredentialOperatorActionResponse(BaseModel):
    schema_version: Literal[1] = 1
    state: Literal[
        "admitted",
        "denied",
        "stale",
        "revoked",
        "already_terminal",
    ]
    reason_code: Identity
    snapshot: CredentialOperatorSnapshotResponse
    receipt: CredentialRevocationReceiptResponse | None = None


__all__ = [
    "CredentialLeaseRequest",
    "CredentialOperatorActionResponse",
    "CredentialOperatorSnapshotResponse",
    "CredentialRevokeRequest",
]
