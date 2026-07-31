"""Canonical output-receipt construction and validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .canonical import (
    canonical_json_bytes,
    canonical_sha256,
    require_exact_fields,
    require_hash_list,
    require_identity,
    require_list,
    require_mapping,
    require_non_negative_int,
    require_omission_list,
    require_optional_sha256,
    require_sha256,
    require_string,
    validate_content_free,
)
from .errors import CapsuleIntegrityError, CapsuleSchemaError
from .models import (
    CostKnowledge,
    OUTPUT_RECEIPT_KIND,
    RUN_CAPSULE_SCHEMA_VERSION,
    OutputReceipt,
)

_PAYLOAD_FIELDS = {
    "run",
    "process",
    "gates",
    "change",
    "approvals",
    "source_to_sink",
    "cost",
    "usage_context",
    "artifacts",
    "warnings",
    "cancellation",
    "omissions",
    "output_sha256",
}
_DOCUMENT_FIELDS = _PAYLOAD_FIELDS | {
    "schema_version",
    "kind",
    "content_free",
    "output_receipt_sha256",
}


def build_output_receipt(payload: Mapping[str, Any]) -> OutputReceipt:
    """Build a strict content-addressed output receipt."""
    data = dict(require_mapping(payload, "output receipt payload"))
    require_exact_fields(data, "output receipt payload", _PAYLOAD_FIELDS)
    document = {
        "schema_version": RUN_CAPSULE_SCHEMA_VERSION,
        "kind": OUTPUT_RECEIPT_KIND,
        "content_free": True,
        **data,
    }
    _validate_output_receipt(document, with_digest=False)
    document["output_receipt_sha256"] = canonical_sha256(document)
    return OutputReceipt(canonical_json_bytes(document))


def parse_output_receipt(payload: object) -> OutputReceipt:
    """Parse and verify one exact schema-v1 output receipt."""
    document = dict(require_mapping(payload, "output receipt"))
    _validate_output_receipt(document, with_digest=True)
    expected = document["output_receipt_sha256"]
    body = {
        key: value for key, value in document.items() if key != "output_receipt_sha256"
    }
    if canonical_sha256(body) != expected:
        raise CapsuleIntegrityError("output receipt digest does not match")
    return OutputReceipt(canonical_json_bytes(document))


def _validate_output_receipt(document: Mapping[str, Any], *, with_digest: bool) -> None:
    expected_fields = (
        _DOCUMENT_FIELDS
        if with_digest
        else _DOCUMENT_FIELDS - {"output_receipt_sha256"}
    )
    require_exact_fields(document, "output receipt", expected_fields)
    if document.get("schema_version") != RUN_CAPSULE_SCHEMA_VERSION:
        raise CapsuleSchemaError("unsupported output receipt schema_version")
    if (
        document.get("kind") != OUTPUT_RECEIPT_KIND
        or document.get("content_free") is not True
    ):
        raise CapsuleSchemaError("output receipt identity or content policy is invalid")
    if with_digest:
        require_sha256(document.get("output_receipt_sha256"), "output_receipt_sha256")
    validate_content_free(document)
    _validate_run(document.get("run"))
    _validate_process(document.get("process"))
    _validate_gates(document.get("gates"))
    _validate_change(document.get("change"))
    _validate_approvals(document.get("approvals"))
    require_hash_list(document.get("source_to_sink"), "source_to_sink")
    _validate_cost(document.get("cost"))
    _validate_usage_context(document.get("usage_context"))
    require_hash_list(document.get("artifacts"), "artifacts")
    _validate_warnings(document.get("warnings"))
    _validate_cancellation(document.get("cancellation"))
    require_omission_list(document.get("omissions"), "omissions")
    require_sha256(document.get("output_sha256"), "output_sha256")


def _validate_run(value: Any) -> None:
    run = require_mapping(value, "run")
    require_exact_fields(run, "run", {"run_id", "attempt_id", "session_id"})
    for key in ("run_id", "attempt_id", "session_id"):
        require_identity(run.get(key), f"run.{key}")


def _validate_process(value: Any) -> None:
    process = require_mapping(value, "process")
    require_exact_fields(
        process, "process", {"mode", "outcome", "exit_code", "receipt_sha256"}
    )
    require_identity(process.get("mode"), "process.mode")
    require_identity(process.get("outcome"), "process.outcome")
    exit_code = process.get("exit_code")
    if exit_code is not None and (
        not isinstance(exit_code, int) or isinstance(exit_code, bool)
    ):
        raise CapsuleSchemaError("process.exit_code must be an integer or null")
    require_sha256(process.get("receipt_sha256"), "process.receipt_sha256")


def _validate_gates(value: Any) -> None:
    gates = require_list(value, "gates", limit=128)
    ids: list[str] = []
    for index, item in enumerate(gates):
        gate = require_mapping(item, f"gates[{index}]")
        require_exact_fields(
            gate, f"gates[{index}]", {"gate_id", "outcome", "receipt_sha256"}
        )
        ids.append(require_identity(gate.get("gate_id"), f"gates[{index}].gate_id"))
        require_identity(gate.get("outcome"), f"gates[{index}].outcome")
        require_sha256(gate.get("receipt_sha256"), f"gates[{index}].receipt_sha256")
    if ids != sorted(set(ids)):
        raise CapsuleSchemaError("gates must be sorted and unique by gate_id")


def _validate_change(value: Any) -> None:
    change = require_mapping(value, "change")
    require_exact_fields(
        change, "change", {"patch_sha256", "base_sha256", "worktree_sha256"}
    )
    for key in ("patch_sha256", "base_sha256", "worktree_sha256"):
        digest = change.get(key)
        if digest != "none":
            require_sha256(digest, f"change.{key}")


def _validate_approvals(value: Any) -> None:
    approvals = require_list(value, "approvals", limit=256)
    ids: list[str] = []
    for index, item in enumerate(approvals):
        approval = require_mapping(item, f"approvals[{index}]")
        require_exact_fields(approval, f"approvals[{index}]", {"id", "response_sha256"})
        ids.append(require_identity(approval.get("id"), f"approvals[{index}].id"))
        require_sha256(
            approval.get("response_sha256"), f"approvals[{index}].response_sha256"
        )
    if ids != sorted(set(ids)):
        raise CapsuleSchemaError("approvals must be sorted and unique by id")


def _validate_cost(value: Any) -> None:
    cost = require_mapping(value, "cost")
    require_exact_fields(
        cost, "cost", {"knowledge", "currency", "amount_micros", "receipt_sha256"}
    )
    try:
        knowledge = CostKnowledge(str(cost.get("knowledge")))
    except ValueError as exc:
        raise CapsuleSchemaError("cost.knowledge is invalid") from exc
    amount = cost.get("amount_micros")
    currency = cost.get("currency")
    if knowledge is CostKnowledge.UNKNOWN:
        if amount is not None or currency is not None:
            raise CapsuleSchemaError(
                "unknown cost must not claim an amount or currency"
            )
    else:
        require_non_negative_int(amount, "cost.amount_micros")
        require_identity(currency, "cost.currency")
    require_optional_sha256(cost.get("receipt_sha256"), "cost.receipt_sha256")


def _validate_usage_context(value: Any) -> None:
    observations = require_mapping(value, "usage_context")
    require_exact_fields(
        observations, "usage_context", {"usage_sha256", "context_sha256"}
    )
    for key in ("usage_sha256", "context_sha256"):
        require_optional_sha256(observations.get(key), f"usage_context.{key}")


def _validate_warnings(value: Any) -> None:
    warnings = require_list(value, "warnings", limit=128)
    normalized = [
        require_string(item, f"warnings[{index}]")
        for index, item in enumerate(warnings)
    ]
    if normalized != sorted(set(normalized)):
        raise CapsuleSchemaError("warnings must be sorted and unique")


def _validate_cancellation(value: Any) -> None:
    cancellation = require_mapping(value, "cancellation")
    require_exact_fields(cancellation, "cancellation", {"state", "reason_code"})
    require_identity(cancellation.get("state"), "cancellation.state")
    if cancellation.get("reason_code") is not None:
        require_identity(cancellation.get("reason_code"), "cancellation.reason_code")
