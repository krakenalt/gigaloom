"""Content-free credential source and scope metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any

from gigaloom.contracts.operational_validation import (
    validate_identity,
    validate_text,
    validate_timestamp,
)
from gigaloom.secrets import SecretReference


MAX_CREDENTIAL_SCOPE_ITEMS = 64
MAX_ACTIVE_SOURCE_LEASES = 1_024
MAX_LEASE_TTL_SECONDS = 3_600


class CredentialQuotaStatus(str, Enum):
    """Knowledge state for provider-owned quota metadata."""

    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class CredentialQuotaMetadata:
    """Read-only quota metadata without billing or account identity."""

    status: CredentialQuotaStatus
    unit: str | None = None
    limit: int | None = None
    remaining: int | None = None
    resets_at: datetime | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CredentialQuotaStatus):
            raise ValueError("credential quota status is invalid")
        if self.status is CredentialQuotaStatus.KNOWN:
            if self.unit is None or self.limit is None or self.remaining is None:
                raise ValueError("known credential quota requires unit and values")
            validate_identity(self.unit, field_name="credential quota unit")
            _bounded_non_negative_int(self.limit, field_name="credential quota limit")
            _bounded_non_negative_int(
                self.remaining,
                field_name="credential quota remaining",
            )
            if self.remaining > self.limit:
                raise ValueError("credential quota remaining exceeds limit")
            if self.reason_code is not None:
                raise ValueError("known credential quota cannot contain a reason")
        else:
            if any(
                value is not None
                for value in (self.unit, self.limit, self.remaining, self.resets_at)
            ):
                raise ValueError("non-known credential quota cannot contain values")
            if self.reason_code is None:
                raise ValueError("non-known credential quota requires a reason")
            validate_identity(
                self.reason_code,
                field_name="credential quota reason",
            )
        if self.resets_at is not None:
            validate_timestamp(self.resets_at, field_name="credential quota reset")

    def to_dict(self) -> dict[str, Any]:
        """Return the exact content-free quota projection."""
        return {
            "status": self.status.value,
            "unit": self.unit,
            "limit": self.limit,
            "remaining": self.remaining,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class CredentialSourceScope:
    """Finite source policy projected without credential material."""

    audiences: tuple[str, ...]
    resources: tuple[str, ...]
    operation_classes: tuple[str, ...]
    scope_digest: str = field(init=False)

    def __post_init__(self) -> None:
        audiences = _scope_items(self.audiences, field_name="credential audience")
        resources = _scope_items(self.resources, field_name="credential resource")
        operations = _scope_items(
            self.operation_classes,
            field_name="credential operation class",
        )
        semantic = {
            "audiences": audiences,
            "resources": resources,
            "operation_classes": operations,
        }
        object.__setattr__(self, "audiences", audiences)
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "operation_classes", operations)
        object.__setattr__(self, "scope_digest", _json_digest(semantic))

    def admits(self, *, audience: str, resource: str, operation_class: str) -> bool:
        """Return whether one exact action is within this source scope."""
        return (
            audience in self.audiences
            and resource in self.resources
            and operation_class in self.operation_classes
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable metadata-only scope projection."""
        return {
            "audiences": list(self.audiences),
            "resources": list(self.resources),
            "operation_classes": list(self.operation_classes),
            "scope_digest": self.scope_digest,
        }


@dataclass(frozen=True, slots=True)
class CredentialSourceRegistration:
    """Private broker registration retaining only an existing SecretRef."""

    source_id: str
    broker_id: str
    provider_identity_ref: str
    secret_reference: SecretReference
    scope: CredentialSourceScope
    quota: CredentialQuotaMetadata
    expires_at: datetime | None = None
    max_lease_ttl_seconds: int = 300
    max_active_leases: int = 1

    def __post_init__(self) -> None:
        for value, label in (
            (self.source_id, "credential source id"),
            (self.broker_id, "credential broker id"),
            (self.provider_identity_ref, "provider identity reference"),
        ):
            validate_identity(value, field_name=label)
        if not isinstance(self.secret_reference, SecretReference):
            raise ValueError("credential source requires a valid SecretReference")
        if not isinstance(self.scope, CredentialSourceScope):
            raise ValueError("credential source scope is invalid")
        if not isinstance(self.quota, CredentialQuotaMetadata):
            raise ValueError("credential quota metadata is invalid")
        if self.expires_at is not None:
            validate_timestamp(self.expires_at, field_name="credential source expiry")
        _bounded_positive_int(
            self.max_lease_ttl_seconds,
            field_name="credential maximum lease ttl",
            maximum=MAX_LEASE_TTL_SECONDS,
        )
        _bounded_positive_int(
            self.max_active_leases,
            field_name="credential maximum active leases",
            maximum=MAX_ACTIVE_SOURCE_LEASES,
        )


@dataclass(frozen=True, slots=True)
class CredentialSourceProjection:
    """Public metadata view with referenced, never copied, identities."""

    source_id: str
    broker_id: str
    provider_identity_ref: str
    secret_ref_id: str
    secret_ref_kind: str
    scope: CredentialSourceScope
    quota: CredentialQuotaMetadata
    expires_at: datetime | None


def credential_source_projection_to_dict(
    value: CredentialSourceProjection,
) -> dict[str, Any]:
    """Serialize a source projection without a SecretRef or secret value."""
    if not isinstance(value, CredentialSourceProjection):
        raise ValueError("credential source projection is invalid")
    return {
        "source_id": value.source_id,
        "broker_id": value.broker_id,
        "provider_identity_ref": value.provider_identity_ref,
        "secret_ref_id": value.secret_ref_id,
        "secret_ref_kind": value.secret_ref_kind,
        "scope": value.scope.to_dict(),
        "quota": value.quota.to_dict(),
        "expires_at": value.expires_at.isoformat() if value.expires_at else None,
    }


def project_credential_source(
    source: CredentialSourceRegistration,
) -> CredentialSourceProjection:
    """Build the only public projection of one private source registration."""
    return CredentialSourceProjection(
        source_id=source.source_id,
        broker_id=source.broker_id,
        provider_identity_ref=source.provider_identity_ref,
        secret_ref_id=source.secret_reference.identity,
        secret_ref_kind=source.secret_reference.kind.value,
        scope=source.scope,
        quota=source.quota,
        expires_at=source.expires_at,
    )


def credential_action_scope_digest(
    *,
    audience: str,
    resource: str,
    operation_class: str,
) -> str:
    """Bind one exact action scope for lease requests and egress checks."""
    for value, label in (
        (audience, "credential audience"),
        (resource, "credential resource"),
        (operation_class, "credential operation class"),
    ):
        validate_text(value, field_name=label, max_chars=2_048)
    return _json_digest(
        {
            "audience": audience,
            "resource": resource,
            "operation_class": operation_class,
        }
    )


def _scope_items(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} scope must be a tuple")
    if not values or len(values) > MAX_CREDENTIAL_SCOPE_ITEMS:
        raise ValueError(f"{field_name} scope size is invalid")
    normalized: list[str] = []
    for value in values:
        validate_text(value, field_name=field_name, max_chars=2_048)
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} scope contains duplicates")
    return tuple(sorted(normalized))


def _bounded_non_negative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _bounded_positive_int(value: int, *, field_name: str, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise ValueError(f"{field_name} must be between 1 and {maximum}")


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CredentialQuotaMetadata",
    "CredentialQuotaStatus",
    "CredentialSourceProjection",
    "CredentialSourceRegistration",
    "CredentialSourceScope",
    "credential_action_scope_digest",
    "credential_source_projection_to_dict",
    "project_credential_source",
]
