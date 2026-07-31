"""Canonical route decision receipt serialization."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping

from gigaloom.review.route_decisions.models import (
    MAX_ROUTE_DECISION_BYTES,
    ROUTE_DECISION_SCHEMA_VERSION,
    RouteDecisionBindingsV1,
    RouteDecisionCostEvidenceV1,
    RouteDecisionCostKnowledge,
    RouteDecisionEligibleRouteV1,
    RouteDecisionError,
    RouteDecisionLatencyEvidenceV1,
    RouteDecisionOutcome,
    RouteDecisionOverrideV1,
    RouteDecisionReceiptV1,
    RouteDecisionRejectedRouteV1,
)


_RECEIPT_FIELDS = {
    "schema_version",
    "route_decision_id",
    "task_digest",
    "context_manifest_digest",
    "project_catalog_digest",
    "launch_profile_digest",
    "capability_catalog_digest",
    "cost_policy_digest",
    "eligible_routes",
    "rejected_routes",
    "recommended_route_id",
    "ranker_id",
    "ranker_version",
    "override",
    "outcome",
    "created_at",
    "receipt_digest",
}


def create_route_decision_receipt(
    advice: object,
    bindings: RouteDecisionBindingsV1,
    *,
    created_at: str,
    route_decision_id: str | None = None,
) -> RouteDecisionReceiptV1:
    """Project one advisor result into a deterministic content-free receipt."""
    eligible_routes = tuple(
        _project_eligible(item)
        for item in _required_sequence_attr(advice, "eligible_routes")
    )
    rejected_routes = tuple(
        _project_rejected(item)
        for item in _required_sequence_attr(advice, "rejected_routes")
    )
    recommended_route_id = _optional_string_attr(advice, "recommended_route_id")
    ranker_id = _required_string_attr(advice, "ranker_id")
    ranker_version = _required_string_attr(advice, "ranker_version")
    override = _project_override(getattr(advice, "override", None))
    outcome = RouteDecisionOutcome(_enum_text(getattr(advice, "outcome", None)))
    body = {
        "schema_version": ROUTE_DECISION_SCHEMA_VERSION,
        "task_digest": bindings.task_digest,
        "context_manifest_digest": bindings.context_manifest_digest,
        "project_catalog_digest": bindings.project_catalog_digest,
        "launch_profile_digest": bindings.launch_profile_digest,
        "capability_catalog_digest": bindings.capability_catalog_digest,
        "cost_policy_digest": bindings.cost_policy_digest,
        "eligible_routes": [_eligible_to_dict(item) for item in eligible_routes],
        "rejected_routes": [_rejected_to_dict(item) for item in rejected_routes],
        "recommended_route_id": recommended_route_id,
        "ranker_id": ranker_id,
        "ranker_version": ranker_version,
        "override": _override_to_dict(override),
        "outcome": outcome.value,
        "created_at": created_at,
    }
    decision_id = route_decision_id or f"route_{_digest(body)[:32]}"
    payload = {**body, "route_decision_id": decision_id}
    return RouteDecisionReceiptV1(
        route_decision_id=decision_id,
        task_digest=bindings.task_digest,
        context_manifest_digest=bindings.context_manifest_digest,
        project_catalog_digest=bindings.project_catalog_digest,
        launch_profile_digest=bindings.launch_profile_digest,
        capability_catalog_digest=bindings.capability_catalog_digest,
        cost_policy_digest=bindings.cost_policy_digest,
        eligible_routes=eligible_routes,
        rejected_routes=rejected_routes,
        recommended_route_id=recommended_route_id,
        ranker_id=ranker_id,
        ranker_version=ranker_version,
        override=override,
        outcome=outcome,
        created_at=created_at,
        receipt_digest=_digest(payload),
    )


def route_decision_receipt_to_dict(
    receipt: RouteDecisionReceiptV1,
) -> dict[str, Any]:
    """Serialize one exact schema-v1 receipt."""
    return {
        "schema_version": receipt.schema_version,
        "route_decision_id": receipt.route_decision_id,
        "task_digest": receipt.task_digest,
        "context_manifest_digest": receipt.context_manifest_digest,
        "project_catalog_digest": receipt.project_catalog_digest,
        "launch_profile_digest": receipt.launch_profile_digest,
        "capability_catalog_digest": receipt.capability_catalog_digest,
        "cost_policy_digest": receipt.cost_policy_digest,
        "eligible_routes": [
            _eligible_to_dict(item) for item in receipt.eligible_routes
        ],
        "rejected_routes": [
            _rejected_to_dict(item) for item in receipt.rejected_routes
        ],
        "recommended_route_id": receipt.recommended_route_id,
        "ranker_id": receipt.ranker_id,
        "ranker_version": receipt.ranker_version,
        "override": _override_to_dict(receipt.override),
        "outcome": receipt.outcome.value,
        "created_at": receipt.created_at,
        "receipt_digest": receipt.receipt_digest,
    }


def route_decision_receipt_from_dict(
    payload: Mapping[str, Any],
) -> RouteDecisionReceiptV1:
    """Strictly parse and integrity-check one schema-v1 receipt."""
    if not isinstance(payload, Mapping) or set(payload) != _RECEIPT_FIELDS:
        raise RouteDecisionError("route decision receipt fields are invalid")
    try:
        receipt = RouteDecisionReceiptV1(
            schema_version=_required_int(payload["schema_version"], "schema_version"),
            route_decision_id=_required_str(
                payload["route_decision_id"], "route_decision_id"
            ),
            task_digest=_required_str(payload["task_digest"], "task_digest"),
            context_manifest_digest=_required_str(
                payload["context_manifest_digest"], "context_manifest_digest"
            ),
            project_catalog_digest=_required_str(
                payload["project_catalog_digest"], "project_catalog_digest"
            ),
            launch_profile_digest=_optional_str(
                payload["launch_profile_digest"], "launch_profile_digest"
            ),
            capability_catalog_digest=_required_str(
                payload["capability_catalog_digest"], "capability_catalog_digest"
            ),
            cost_policy_digest=_required_str(
                payload["cost_policy_digest"], "cost_policy_digest"
            ),
            eligible_routes=tuple(
                _eligible_from_dict(item)
                for item in _object_list(payload["eligible_routes"], "eligible_routes")
            ),
            rejected_routes=tuple(
                _rejected_from_dict(item)
                for item in _object_list(payload["rejected_routes"], "rejected_routes")
            ),
            recommended_route_id=_optional_str(
                payload["recommended_route_id"], "recommended_route_id"
            ),
            ranker_id=_required_str(payload["ranker_id"], "ranker_id"),
            ranker_version=_required_str(payload["ranker_version"], "ranker_version"),
            override=_override_from_dict(payload["override"]),
            outcome=RouteDecisionOutcome(_required_str(payload["outcome"], "outcome")),
            created_at=_required_str(payload["created_at"], "created_at"),
            receipt_digest=_required_str(payload["receipt_digest"], "receipt_digest"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RouteDecisionError):
            raise
        raise RouteDecisionError("route decision receipt payload is invalid") from exc
    expected = _digest(
        {
            key: value
            for key, value in route_decision_receipt_to_dict(receipt).items()
            if key != "receipt_digest"
        }
    )
    if receipt.receipt_digest != expected:
        raise RouteDecisionError("route decision receipt digest mismatch")
    return receipt


def canonical_route_decision_bytes(receipt: RouteDecisionReceiptV1) -> bytes:
    """Return bounded canonical JSON bytes after integrity verification."""
    payload = route_decision_receipt_to_dict(receipt)
    route_decision_receipt_from_dict(payload)
    encoded = _canonical_json(payload)
    if len(encoded) + 1 > MAX_ROUTE_DECISION_BYTES:
        raise RouteDecisionError("route decision receipt exceeds size limit")
    return encoded + b"\n"


def _project_eligible(value: object) -> RouteDecisionEligibleRouteV1:
    cost = getattr(value, "cost", None)
    if cost is None:
        raise RouteDecisionError("eligible route cost projection is missing")
    latency_value = getattr(value, "latency", None)
    latency = None
    if latency_value is not None:
        latency = RouteDecisionLatencyEvidenceV1(
            comparison_group=_required_string_attr(latency_value, "comparison_group"),
            p95_milliseconds=_required_int_attr(latency_value, "p95_milliseconds"),
            evidence_digest=_required_string_attr(latency_value, "evidence_digest"),
        )
    grade = getattr(value, "compatibility_grade", None)
    grade_name = getattr(grade, "name", None)
    compatibility_grade = (
        str(grade_name).lower() if grade_name is not None else str(grade)
    )
    return RouteDecisionEligibleRouteV1(
        route_id=_required_string_attr(value, "route_id"),
        agent_id=_required_string_attr(value, "agent_id"),
        profile_digest=_required_string_attr(value, "profile_digest"),
        capability_snapshot_digest=_required_string_attr(
            value, "capability_snapshot_digest"
        ),
        account_digest=_required_string_attr(value, "account_digest"),
        transport_class=_required_string_attr(value, "transport_class"),
        cost=RouteDecisionCostEvidenceV1(
            knowledge=RouteDecisionCostKnowledge(
                _enum_text(getattr(cost, "knowledge", None))
            ),
            currency=_optional_string_attr(cost, "currency"),
            amount=_optional_decimal_attr(cost, "amount"),
            headroom=_optional_decimal_attr(cost, "headroom"),
        ),
        compatibility_grade=compatibility_grade,
        policy_priority=_required_int_attr(value, "policy_priority"),
        explicit_preference_match=_required_bool_attr(
            value, "explicit_preference_match"
        ),
        exact_capability_match=_required_bool_attr(value, "exact_capability_match"),
        latency=latency,
        rank=_optional_int_attr(value, "rank"),
    )


def _project_rejected(value: object) -> RouteDecisionRejectedRouteV1:
    reason_values = _required_sequence_attr(value, "reason_codes")
    return RouteDecisionRejectedRouteV1(
        route_id=_required_string_attr(value, "route_id"),
        agent_id=_required_string_attr(value, "agent_id"),
        reason_codes=tuple(_enum_text(item) for item in reason_values),
    )


def _project_override(value: object | None) -> RouteDecisionOverrideV1 | None:
    if value is None:
        return None
    return RouteDecisionOverrideV1(
        route_id=_required_string_attr(value, "route_id"),
        reason_code=_required_string_attr(value, "reason_code"),
        created_at=_required_string_attr(value, "created_at"),
    )


def _eligible_to_dict(item: RouteDecisionEligibleRouteV1) -> dict[str, Any]:
    latency = None
    if item.latency is not None:
        latency = {
            "comparison_group": item.latency.comparison_group,
            "p95_milliseconds": item.latency.p95_milliseconds,
            "evidence_digest": item.latency.evidence_digest,
        }
    return {
        "route_id": item.route_id,
        "agent_id": item.agent_id,
        "profile_digest": item.profile_digest,
        "capability_snapshot_digest": item.capability_snapshot_digest,
        "account_digest": item.account_digest,
        "transport_class": item.transport_class,
        "cost": {
            "knowledge": item.cost.knowledge.value,
            "currency": item.cost.currency,
            "amount": _decimal_to_str(item.cost.amount),
            "headroom": _decimal_to_str(item.cost.headroom),
        },
        "compatibility_grade": item.compatibility_grade,
        "policy_priority": item.policy_priority,
        "explicit_preference_match": item.explicit_preference_match,
        "exact_capability_match": item.exact_capability_match,
        "latency": latency,
        "rank": item.rank,
    }


def _eligible_from_dict(value: Mapping[str, Any]) -> RouteDecisionEligibleRouteV1:
    _exact_fields(
        value,
        {
            "route_id",
            "agent_id",
            "profile_digest",
            "capability_snapshot_digest",
            "account_digest",
            "transport_class",
            "cost",
            "compatibility_grade",
            "policy_priority",
            "explicit_preference_match",
            "exact_capability_match",
            "latency",
            "rank",
        },
        field_name="eligible route",
    )
    cost = _mapping(value["cost"], "eligible route cost")
    _exact_fields(
        cost,
        {"knowledge", "currency", "amount", "headroom"},
        field_name="eligible route cost",
    )
    latency_payload = value["latency"]
    latency = None
    if latency_payload is not None:
        latency_mapping = _mapping(latency_payload, "eligible route latency")
        _exact_fields(
            latency_mapping,
            {"comparison_group", "p95_milliseconds", "evidence_digest"},
            field_name="eligible route latency",
        )
        latency = RouteDecisionLatencyEvidenceV1(
            comparison_group=_required_str(
                latency_mapping["comparison_group"], "comparison_group"
            ),
            p95_milliseconds=_required_int(
                latency_mapping["p95_milliseconds"], "p95_milliseconds"
            ),
            evidence_digest=_required_str(
                latency_mapping["evidence_digest"], "evidence_digest"
            ),
        )
    return RouteDecisionEligibleRouteV1(
        route_id=_required_str(value["route_id"], "route_id"),
        agent_id=_required_str(value["agent_id"], "agent_id"),
        profile_digest=_required_str(value["profile_digest"], "profile_digest"),
        capability_snapshot_digest=_required_str(
            value["capability_snapshot_digest"], "capability_snapshot_digest"
        ),
        account_digest=_required_str(value["account_digest"], "account_digest"),
        transport_class=_required_str(value["transport_class"], "transport_class"),
        cost=RouteDecisionCostEvidenceV1(
            knowledge=RouteDecisionCostKnowledge(
                _required_str(cost["knowledge"], "cost knowledge")
            ),
            currency=_optional_str(cost["currency"], "cost currency"),
            amount=_optional_decimal(cost["amount"], "cost amount"),
            headroom=_optional_decimal(cost["headroom"], "cost headroom"),
        ),
        compatibility_grade=_required_str(
            value["compatibility_grade"], "compatibility_grade"
        ),
        policy_priority=_required_int(value["policy_priority"], "policy_priority"),
        explicit_preference_match=_required_bool(
            value["explicit_preference_match"], "explicit_preference_match"
        ),
        exact_capability_match=_required_bool(
            value["exact_capability_match"], "exact_capability_match"
        ),
        latency=latency,
        rank=_optional_int(value["rank"], "rank"),
    )


def _rejected_to_dict(item: RouteDecisionRejectedRouteV1) -> dict[str, Any]:
    return {
        "route_id": item.route_id,
        "agent_id": item.agent_id,
        "reason_codes": list(item.reason_codes),
    }


def _rejected_from_dict(value: Mapping[str, Any]) -> RouteDecisionRejectedRouteV1:
    _exact_fields(
        value,
        {"route_id", "agent_id", "reason_codes"},
        field_name="rejected route",
    )
    reason_codes = value["reason_codes"]
    if not isinstance(reason_codes, list):
        raise RouteDecisionError("rejected route reason_codes must be a list")
    return RouteDecisionRejectedRouteV1(
        route_id=_required_str(value["route_id"], "route_id"),
        agent_id=_required_str(value["agent_id"], "agent_id"),
        reason_codes=tuple(_required_str(item, "reason_code") for item in reason_codes),
    )


def _override_to_dict(
    value: RouteDecisionOverrideV1 | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "route_id": value.route_id,
        "reason_code": value.reason_code,
        "created_at": value.created_at,
    }


def _override_from_dict(value: Any) -> RouteDecisionOverrideV1 | None:
    if value is None:
        return None
    mapping = _mapping(value, "override")
    _exact_fields(
        mapping,
        {"route_id", "reason_code", "created_at"},
        field_name="override",
    )
    return RouteDecisionOverrideV1(
        route_id=_required_str(mapping["route_id"], "override route_id"),
        reason_code=_required_str(mapping["reason_code"], "override reason_code"),
        created_at=_required_str(mapping["created_at"], "override created_at"),
    )


def _digest(value: Mapping[str, Any]) -> str:
    encoded = _canonical_json(value)
    return hashlib.sha256(b"gigaloom.route_decision.v1\0" + encoded).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _optional_decimal(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RouteDecisionError(f"{field_name} must be a decimal string")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise RouteDecisionError(f"{field_name} is invalid") from exc


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RouteDecisionError(f"{field_name} must be an object")
    return value


def _object_list(value: Any, field_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or len(value) > 1_000:
        raise RouteDecisionError(f"{field_name} must be a bounded list")
    return [_mapping(item, field_name) for item in value]


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, field_name: str
) -> None:
    if set(value) != expected:
        raise RouteDecisionError(f"{field_name} fields are invalid")


def _required_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise RouteDecisionError(f"{field_name} must be a string")
    return value


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_str(value, field_name)


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RouteDecisionError(f"{field_name} must be an integer")
    return value


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, field_name)


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RouteDecisionError(f"{field_name} must be a boolean")
    return value


def _required_string_attr(value: object, field_name: str) -> str:
    return _required_str(getattr(value, field_name, None), field_name)


def _optional_string_attr(value: object, field_name: str) -> str | None:
    return _optional_str(getattr(value, field_name, None), field_name)


def _required_int_attr(value: object, field_name: str) -> int:
    return _required_int(getattr(value, field_name, None), field_name)


def _optional_int_attr(value: object, field_name: str) -> int | None:
    return _optional_int(getattr(value, field_name, None), field_name)


def _required_bool_attr(value: object, field_name: str) -> bool:
    return _required_bool(getattr(value, field_name, None), field_name)


def _optional_decimal_attr(value: object, field_name: str) -> Decimal | None:
    candidate = getattr(value, field_name, None)
    if candidate is None:
        return None
    if not isinstance(candidate, Decimal):
        raise RouteDecisionError(f"{field_name} must be a Decimal")
    return candidate


def _required_sequence_attr(value: object, field_name: str) -> tuple[object, ...]:
    candidate = getattr(value, field_name, None)
    if not isinstance(candidate, tuple):
        raise RouteDecisionError(f"{field_name} must be a tuple")
    return candidate


def _enum_text(value: object) -> str:
    candidate = getattr(value, "value", value)
    if not isinstance(candidate, str):
        raise RouteDecisionError("enum projection must be a string")
    return candidate


__all__ = [
    "canonical_route_decision_bytes",
    "create_route_decision_receipt",
    "route_decision_receipt_from_dict",
    "route_decision_receipt_to_dict",
]
