"""Strict serialization for public cost and budget contracts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping

from gigaloom.contracts.cost import (
    COST_CONTRACT_SCHEMA_VERSION,
    BudgetAdmission,
    BudgetAdmissionDecision,
    BudgetLease,
    BudgetPolicy,
    BudgetPolicyKind,
    CostConfidence,
    CostObservation,
    CostReceipt,
    CostReceiptOutcome,
    SubscriptionQuotaObservation,
    TokenObservation,
)


def budget_policy_to_dict(value: BudgetPolicy) -> dict[str, Any]:
    """Serialize one explicit budget policy."""
    _require_instance(value, BudgetPolicy, field_name="budget policy")
    return {
        "schema_version": value.schema_version,
        "kind": value.kind.value,
        "currency": value.currency,
        "amount": _decimal_to_text(value.amount),
    }


def budget_policy_from_dict(data: Mapping[str, Any]) -> BudgetPolicy:
    """Strictly parse one budget policy."""
    payload = _strict_mapping(
        data,
        {"schema_version", "kind", "currency", "amount"},
        field_name="budget policy",
    )
    return BudgetPolicy(
        kind=_enum_value(
            payload.get("kind"),
            BudgetPolicyKind,
            field_name="budget policy kind",
        ),
        currency=_optional_text(payload.get("currency")),
        amount=_optional_decimal(payload.get("amount"), field_name="budget amount"),
        schema_version=_schema_version(payload, field_name="budget policy"),
    )


def cost_observation_to_dict(value: CostObservation) -> dict[str, Any]:
    """Serialize one monetary observation without inventing unknown amounts."""
    _require_instance(value, CostObservation, field_name="cost observation")
    return {
        **_observation_identity_to_dict(value),
        "currency": value.currency,
        "amount": _decimal_to_text(value.amount),
        "reason_code": value.reason_code,
    }


def cost_observation_from_dict(data: Mapping[str, Any]) -> CostObservation:
    """Strictly parse one monetary observation."""
    payload = _strict_mapping(
        data,
        _OBSERVATION_IDENTITY_FIELDS | {"currency", "amount", "reason_code"},
        field_name="cost observation",
    )
    return CostObservation(
        **_observation_identity_from_dict(payload),
        currency=_optional_text(payload.get("currency")),
        amount=_optional_decimal(payload.get("amount"), field_name="cost amount"),
        reason_code=_optional_text(payload.get("reason_code")),
    )


def token_observation_to_dict(value: TokenObservation) -> dict[str, Any]:
    """Serialize one token observation."""
    _require_instance(value, TokenObservation, field_name="token observation")
    return {
        **_observation_identity_to_dict(value),
        "input_tokens": value.input_tokens,
        "output_tokens": value.output_tokens,
        "total_tokens": value.total_tokens,
        "reason_code": value.reason_code,
    }


def token_observation_from_dict(data: Mapping[str, Any]) -> TokenObservation:
    """Strictly parse one token observation."""
    payload = _strict_mapping(
        data,
        _OBSERVATION_IDENTITY_FIELDS
        | {"input_tokens", "output_tokens", "total_tokens", "reason_code"},
        field_name="token observation",
    )
    return TokenObservation(
        **_observation_identity_from_dict(payload),
        input_tokens=_optional_int(payload.get("input_tokens"), "input_tokens"),
        output_tokens=_optional_int(payload.get("output_tokens"), "output_tokens"),
        total_tokens=_optional_int(payload.get("total_tokens"), "total_tokens"),
        reason_code=_optional_text(payload.get("reason_code")),
    )


def subscription_quota_observation_to_dict(
    value: SubscriptionQuotaObservation,
) -> dict[str, Any]:
    """Serialize subscription quota separately from monetary cost."""
    _require_instance(value, SubscriptionQuotaObservation, field_name="quota")
    return {
        **_observation_identity_to_dict(value, include_model=False),
        "quota_name": value.quota_name,
        "unit": value.unit,
        "used": _decimal_to_text(value.used),
        "limit": _decimal_to_text(value.limit),
        "remaining": _decimal_to_text(value.remaining),
        "reason_code": value.reason_code,
    }


def subscription_quota_observation_from_dict(
    data: Mapping[str, Any],
) -> SubscriptionQuotaObservation:
    """Strictly parse one subscription quota observation."""
    fields = (_OBSERVATION_IDENTITY_FIELDS - {"model_id"}) | {
        "quota_name",
        "unit",
        "used",
        "limit",
        "remaining",
        "reason_code",
    }
    payload = _strict_mapping(data, fields, field_name="quota observation")
    return SubscriptionQuotaObservation(
        **_observation_identity_from_dict(payload, include_model=False),
        quota_name=_required_text(payload.get("quota_name"), "quota name"),
        unit=_required_text(payload.get("unit"), "quota unit"),
        used=_optional_decimal(payload.get("used"), field_name="quota used"),
        limit=_optional_decimal(payload.get("limit"), field_name="quota limit"),
        remaining=_optional_decimal(
            payload.get("remaining"),
            field_name="quota remaining",
        ),
        reason_code=_optional_text(payload.get("reason_code")),
    )


def budget_admission_to_dict(value: BudgetAdmission) -> dict[str, Any]:
    """Serialize one pre-spawn admission result."""
    _require_instance(value, BudgetAdmission, field_name="budget admission")
    return {
        "schema_version": value.schema_version,
        "id": value.id,
        "policy": budget_policy_to_dict(value.policy),
        "decision": value.decision.value,
        "route_class": value.route_class,
        "requested_cost": cost_observation_to_dict(value.requested_cost),
        "reason_code": value.reason_code,
        "created_at": value.created_at,
        "price_source_digest": value.price_source_digest,
    }


def budget_admission_from_dict(data: Mapping[str, Any]) -> BudgetAdmission:
    """Strictly parse one admission result."""
    payload = _strict_mapping(
        data,
        {
            "schema_version",
            "id",
            "policy",
            "decision",
            "route_class",
            "requested_cost",
            "reason_code",
            "created_at",
            "price_source_digest",
        },
        field_name="budget admission",
    )
    return BudgetAdmission(
        id=_required_text(payload.get("id"), "admission id"),
        policy=budget_policy_from_dict(_mapping(payload.get("policy"), "policy")),
        decision=_enum_value(
            payload.get("decision"),
            BudgetAdmissionDecision,
            field_name="budget admission decision",
        ),
        route_class=_required_text(payload.get("route_class"), "route class"),
        requested_cost=cost_observation_from_dict(
            _mapping(payload.get("requested_cost"), "requested_cost")
        ),
        reason_code=_required_text(payload.get("reason_code"), "reason code"),
        created_at=_required_text(payload.get("created_at"), "created_at"),
        price_source_digest=_optional_text(payload.get("price_source_digest")),
        schema_version=_schema_version(payload, field_name="budget admission"),
    )


def budget_lease_to_dict(value: BudgetLease) -> dict[str, Any]:
    """Serialize one parent-bound budget lease."""
    _require_instance(value, BudgetLease, field_name="budget lease")
    return {
        "schema_version": value.schema_version,
        "id": value.id,
        "admission_id": value.admission_id,
        "parent_lease_id": value.parent_lease_id,
        "limit": budget_policy_to_dict(value.limit),
        "issued_at": value.issued_at,
        "expires_at": value.expires_at,
    }


def budget_lease_from_dict(data: Mapping[str, Any]) -> BudgetLease:
    """Strictly parse one budget lease."""
    payload = _strict_mapping(
        data,
        {
            "schema_version",
            "id",
            "admission_id",
            "parent_lease_id",
            "limit",
            "issued_at",
            "expires_at",
        },
        field_name="budget lease",
    )
    return BudgetLease(
        id=_required_text(payload.get("id"), "lease id"),
        admission_id=_required_text(payload.get("admission_id"), "admission id"),
        parent_lease_id=_optional_text(payload.get("parent_lease_id")),
        limit=budget_policy_from_dict(_mapping(payload.get("limit"), "lease limit")),
        issued_at=_required_text(payload.get("issued_at"), "issued_at"),
        expires_at=_required_text(payload.get("expires_at"), "expires_at"),
        schema_version=_schema_version(payload, field_name="budget lease"),
    )


def cost_receipt_to_dict(value: CostReceipt) -> dict[str, Any]:
    """Serialize one terminal cost receipt."""
    _require_instance(value, CostReceipt, field_name="cost receipt")
    return {
        "schema_version": value.schema_version,
        "id": value.id,
        "lease_id": value.lease_id,
        "outcome": value.outcome.value,
        "final_cost": cost_observation_to_dict(value.final_cost),
        "closed_at": value.closed_at,
        "reason_code": value.reason_code,
    }


def cost_receipt_from_dict(data: Mapping[str, Any]) -> CostReceipt:
    """Strictly parse one terminal cost receipt."""
    payload = _strict_mapping(
        data,
        {
            "schema_version",
            "id",
            "lease_id",
            "outcome",
            "final_cost",
            "closed_at",
            "reason_code",
        },
        field_name="cost receipt",
    )
    return CostReceipt(
        id=_required_text(payload.get("id"), "receipt id"),
        lease_id=_required_text(payload.get("lease_id"), "lease id"),
        outcome=_enum_value(
            payload.get("outcome"),
            CostReceiptOutcome,
            field_name="cost receipt outcome",
        ),
        final_cost=cost_observation_from_dict(
            _mapping(payload.get("final_cost"), "final_cost")
        ),
        closed_at=_required_text(payload.get("closed_at"), "closed_at"),
        reason_code=_required_text(payload.get("reason_code"), "reason code"),
        schema_version=_schema_version(payload, field_name="cost receipt"),
    )


_OBSERVATION_IDENTITY_FIELDS = {
    "schema_version",
    "provider_id",
    "model_id",
    "route_class",
    "confidence",
    "source",
    "source_digest",
    "observed_at",
}


def _observation_identity_to_dict(
    value: CostObservation | TokenObservation | SubscriptionQuotaObservation,
    *,
    include_model: bool = True,
) -> dict[str, Any]:
    payload = {
        "schema_version": value.schema_version,
        "provider_id": value.provider_id,
        "route_class": value.route_class,
        "confidence": value.confidence.value,
        "source": value.source,
        "source_digest": value.source_digest,
        "observed_at": value.observed_at,
    }
    if include_model:
        payload["model_id"] = getattr(value, "model_id", None)
    return payload


def _observation_identity_from_dict(
    payload: Mapping[str, Any],
    *,
    include_model: bool = True,
) -> dict[str, Any]:
    result = {
        "provider_id": _required_text(payload.get("provider_id"), "provider id"),
        "route_class": _required_text(payload.get("route_class"), "route class"),
        "confidence": _enum_value(
            payload.get("confidence"),
            CostConfidence,
            field_name="cost confidence",
        ),
        "source": _required_text(payload.get("source"), "observation source"),
        "source_digest": _required_text(
            payload.get("source_digest"),
            "source digest",
        ),
        "observed_at": _required_text(payload.get("observed_at"), "observed_at"),
        "schema_version": _schema_version(payload, field_name="observation"),
    }
    if include_model:
        result["model_id"] = _required_text(payload.get("model_id"), "model id")
    return result


def _strict_mapping(
    data: Mapping[str, Any],
    allowed: set[str],
    *,
    field_name: str,
) -> Mapping[str, Any]:
    payload = _mapping(data, field_name)
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unknown {field_name} fields: {', '.join(sorted(unknown))}")
    return payload


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _schema_version(data: Mapping[str, Any], *, field_name: str) -> int:
    value = data.get("schema_version")
    if value != COST_CONTRACT_SCHEMA_VERSION:
        raise ValueError(f"unsupported {field_name} schema_version")
    return value


def _enum_value(value: Any, enum_type: type[Enum], *, field_name: str) -> Any:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is invalid")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is invalid") from exc


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text field must be a string or null")
    return value


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer or null")
    return value


def _optional_decimal(value: Any, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a decimal string or null")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a decimal string or null") from exc


def _decimal_to_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _require_instance(value: Any, expected: type[Any], *, field_name: str) -> None:
    if not isinstance(value, expected):
        raise ValueError(f"{field_name} has an invalid type")


__all__ = [
    "budget_admission_from_dict",
    "budget_admission_to_dict",
    "budget_lease_from_dict",
    "budget_lease_to_dict",
    "budget_policy_from_dict",
    "budget_policy_to_dict",
    "cost_observation_from_dict",
    "cost_observation_to_dict",
    "cost_receipt_from_dict",
    "cost_receipt_to_dict",
    "subscription_quota_observation_from_dict",
    "subscription_quota_observation_to_dict",
    "token_observation_from_dict",
    "token_observation_to_dict",
]
