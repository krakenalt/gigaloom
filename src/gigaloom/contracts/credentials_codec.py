"""Strict wire codecs for secret-free credential contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar

from gigaloom.contracts.credentials import (
    CredentialInjectionReceiptV1,
    CredentialInjectionStatus,
    CredentialLeaseDecisionStatus,
    CredentialLeaseDecisionV1,
    CredentialLeaseRequestV1,
    CredentialLeaseStatus,
    CredentialLeaseV1,
    CredentialRevocationReceiptV1,
    CredentialRevocationStatus,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def credential_lease_request_to_dict(
    value: CredentialLeaseRequestV1,
) -> dict[str, Any]:
    """Serialize one lease request without secret material."""
    return {
        "schema_version": value.schema_version,
        "request_id": value.request_id,
        "secret_ref_id": value.secret_ref_id,
        "broker_id": value.broker_id,
        "audience": value.audience,
        "resource": value.resource,
        "operation_class": value.operation_class,
        "requested_at": value.requested_at.isoformat(),
        "expires_at": value.expires_at.isoformat(),
        "parent_run_id": value.parent_run_id,
        "policy_digest": value.policy_digest,
        "scope_digest": value.scope_digest,
        "parent_lease_id": value.parent_lease_id,
    }


def credential_lease_request_from_dict(
    payload: Mapping[str, Any],
) -> CredentialLeaseRequestV1:
    """Decode a strict lease request object."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "request_id",
            "secret_ref_id",
            "broker_id",
            "audience",
            "resource",
            "operation_class",
            "requested_at",
            "expires_at",
            "parent_run_id",
            "policy_digest",
            "scope_digest",
            "parent_lease_id",
        },
        field_name="credential lease request",
    )
    return CredentialLeaseRequestV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        request_id=_string(value["request_id"], "request_id"),
        secret_ref_id=_string(value["secret_ref_id"], "secret_ref_id"),
        broker_id=_string(value["broker_id"], "broker_id"),
        audience=_string(value["audience"], "audience"),
        resource=_string(value["resource"], "resource"),
        operation_class=_string(value["operation_class"], "operation_class"),
        requested_at=parse_timestamp(value["requested_at"], field_name="requested_at"),
        expires_at=parse_timestamp(value["expires_at"], field_name="expires_at"),
        parent_run_id=_string(value["parent_run_id"], "parent_run_id"),
        policy_digest=_string(value["policy_digest"], "policy_digest"),
        scope_digest=_string(value["scope_digest"], "scope_digest"),
        parent_lease_id=_optional_string(value["parent_lease_id"], "parent_lease_id"),
    )


def credential_lease_to_dict(value: CredentialLeaseV1) -> dict[str, Any]:
    """Serialize exactly the persistable credential lease metadata."""
    return {
        "schema_version": value.schema_version,
        "lease_id": value.lease_id,
        "secret_ref_id": value.secret_ref_id,
        "broker_id": value.broker_id,
        "audience": value.audience,
        "resource": value.resource,
        "operation_class": value.operation_class,
        "issued_at": value.issued_at.isoformat(),
        "expires_at": value.expires_at.isoformat(),
        "parent_run_id": value.parent_run_id,
        "policy_digest": value.policy_digest,
        "scope_digest": value.scope_digest,
        "status": value.status.value,
    }


def credential_lease_from_dict(payload: Mapping[str, Any]) -> CredentialLeaseV1:
    """Decode a strict persistable credential lease."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "lease_id",
            "secret_ref_id",
            "broker_id",
            "audience",
            "resource",
            "operation_class",
            "issued_at",
            "expires_at",
            "parent_run_id",
            "policy_digest",
            "scope_digest",
            "status",
        },
        field_name="credential lease",
    )
    return CredentialLeaseV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        lease_id=_string(value["lease_id"], "lease_id"),
        secret_ref_id=_string(value["secret_ref_id"], "secret_ref_id"),
        broker_id=_string(value["broker_id"], "broker_id"),
        audience=_string(value["audience"], "audience"),
        resource=_string(value["resource"], "resource"),
        operation_class=_string(value["operation_class"], "operation_class"),
        issued_at=parse_timestamp(value["issued_at"], field_name="issued_at"),
        expires_at=parse_timestamp(value["expires_at"], field_name="expires_at"),
        parent_run_id=_string(value["parent_run_id"], "parent_run_id"),
        policy_digest=_string(value["policy_digest"], "policy_digest"),
        scope_digest=_string(value["scope_digest"], "scope_digest"),
        status=_enum(CredentialLeaseStatus, value["status"], "status"),
    )


def credential_lease_decision_to_dict(
    value: CredentialLeaseDecisionV1,
) -> dict[str, Any]:
    """Serialize one credential admission decision."""
    return {
        "schema_version": value.schema_version,
        "decision_id": value.decision_id,
        "request_id": value.request_id,
        "status": value.status.value,
        "reason_code": value.reason_code,
        "decided_at": value.decided_at.isoformat(),
        "policy_digest": value.policy_digest,
        "lease": credential_lease_to_dict(value.lease) if value.lease else None,
    }


def credential_lease_decision_from_dict(
    payload: Mapping[str, Any],
) -> CredentialLeaseDecisionV1:
    """Decode one strict credential admission decision."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "decision_id",
            "request_id",
            "status",
            "reason_code",
            "decided_at",
            "policy_digest",
            "lease",
        },
        field_name="credential lease decision",
    )
    lease_payload = value["lease"]
    if lease_payload is not None and not isinstance(lease_payload, Mapping):
        raise ValueError("credential decision lease must be an object or null")
    return CredentialLeaseDecisionV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        decision_id=_string(value["decision_id"], "decision_id"),
        request_id=_string(value["request_id"], "request_id"),
        status=_enum(CredentialLeaseDecisionStatus, value["status"], "status"),
        reason_code=_string(value["reason_code"], "reason_code"),
        decided_at=parse_timestamp(value["decided_at"], field_name="decided_at"),
        policy_digest=_string(value["policy_digest"], "policy_digest"),
        lease=(
            credential_lease_from_dict(lease_payload)
            if isinstance(lease_payload, Mapping)
            else None
        ),
    )


def credential_injection_receipt_to_dict(
    value: CredentialInjectionReceiptV1,
) -> dict[str, Any]:
    """Serialize one content-free injection receipt."""
    return {
        "schema_version": value.schema_version,
        "receipt_id": value.receipt_id,
        "lease_id": value.lease_id,
        "request_digest": value.request_digest,
        "destination_digest": value.destination_digest,
        "operation_digest": value.operation_digest,
        "status": value.status.value,
        "reason_code": value.reason_code,
        "injected_at": value.injected_at.isoformat(),
        "content_free": value.content_free,
    }


def credential_injection_receipt_from_dict(
    payload: Mapping[str, Any],
) -> CredentialInjectionReceiptV1:
    """Decode one strict injection receipt."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "receipt_id",
            "lease_id",
            "request_digest",
            "destination_digest",
            "operation_digest",
            "status",
            "reason_code",
            "injected_at",
            "content_free",
        },
        field_name="credential injection receipt",
    )
    return CredentialInjectionReceiptV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        receipt_id=_string(value["receipt_id"], "receipt_id"),
        lease_id=_string(value["lease_id"], "lease_id"),
        request_digest=_string(value["request_digest"], "request_digest"),
        destination_digest=_string(value["destination_digest"], "destination_digest"),
        operation_digest=_string(value["operation_digest"], "operation_digest"),
        status=_enum(CredentialInjectionStatus, value["status"], "status"),
        reason_code=_string(value["reason_code"], "reason_code"),
        injected_at=parse_timestamp(value["injected_at"], field_name="injected_at"),
        content_free=_boolean(value["content_free"], "content_free"),
    )


def credential_revocation_receipt_to_dict(
    value: CredentialRevocationReceiptV1,
) -> dict[str, Any]:
    """Serialize one content-free revocation receipt."""
    return {
        "schema_version": value.schema_version,
        "receipt_id": value.receipt_id,
        "lease_id": value.lease_id,
        "status": value.status.value,
        "reason_code": value.reason_code,
        "revoked_at": value.revoked_at.isoformat(),
        "idempotency_key_digest": value.idempotency_key_digest,
        "content_free": value.content_free,
    }


def credential_revocation_receipt_from_dict(
    payload: Mapping[str, Any],
) -> CredentialRevocationReceiptV1:
    """Decode one strict revocation receipt."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "receipt_id",
            "lease_id",
            "status",
            "reason_code",
            "revoked_at",
            "idempotency_key_digest",
            "content_free",
        },
        field_name="credential revocation receipt",
    )
    return CredentialRevocationReceiptV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        receipt_id=_string(value["receipt_id"], "receipt_id"),
        lease_id=_string(value["lease_id"], "lease_id"),
        status=_enum(CredentialRevocationStatus, value["status"], "status"),
        reason_code=_string(value["reason_code"], "reason_code"),
        revoked_at=parse_timestamp(value["revoked_at"], field_name="revoked_at"),
        idempotency_key_digest=_string(
            value["idempotency_key_digest"],
            "idempotency_key_digest",
        ),
        content_free=_boolean(value["content_free"], "content_free"),
    )


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value
