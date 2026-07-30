"""Strict canonical serialization for Reviewed Arena evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping, cast

from .models import (
    ARBITRATION_RECEIPT_KIND,
    CANDIDATE_EVIDENCE_KIND,
    REVIEWED_ARENA_SCHEMA_VERSION,
    ArenaOutcome,
    ArbitrationReceipt,
    CandidateEvidence,
    CandidateEvidenceDigest,
    CandidateIsolation,
    CandidateStatus,
    DeterministicGateReceipt,
    EvidenceBinding,
    GateOutcome,
)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}")


def candidate_evidence_to_dict(value: CandidateEvidence) -> dict[str, object]:
    """Serialize one candidate evidence document."""
    return {
        "schema_version": REVIEWED_ARENA_SCHEMA_VERSION,
        "kind": CANDIDATE_EVIDENCE_KIND,
        "evidence_id": value.evidence_id,
        "arena_id": value.arena_id,
        "candidate_id": value.candidate_id,
        "ordinal": value.ordinal,
        "owner_id": value.owner_id,
        "workspace_id": value.workspace_id,
        "base_revision": value.base_revision,
        "run_id": value.run_id,
        "session_id": value.session_id,
        "status": value.status.value,
        "isolation": _isolation_to_dict(value.isolation),
        "run_capsule": _binding_to_dict(value.run_capsule),
        "context_manifest": _binding_to_dict(value.context_manifest),
        "change_set": _binding_to_dict(value.change_set),
        "cost_lease_id": value.cost_lease_id,
        "cost_receipt": _binding_to_dict(value.cost_receipt),
        "gate": _gate_to_dict(value.gate),
        "created_at": value.created_at,
        "evidence_sha256": value.evidence_sha256,
    }


def candidate_evidence_from_dict(payload: object) -> CandidateEvidence:
    """Parse and verify one candidate evidence document."""
    data = _mapping(payload, "CandidateEvidence")
    _exact_fields(
        data,
        {
            "schema_version",
            "kind",
            "evidence_id",
            "arena_id",
            "candidate_id",
            "ordinal",
            "owner_id",
            "workspace_id",
            "base_revision",
            "run_id",
            "session_id",
            "status",
            "isolation",
            "run_capsule",
            "context_manifest",
            "change_set",
            "cost_lease_id",
            "cost_receipt",
            "gate",
            "created_at",
            "evidence_sha256",
        },
        "CandidateEvidence",
    )
    if data["schema_version"] != REVIEWED_ARENA_SCHEMA_VERSION:
        raise ValueError("unsupported CandidateEvidence schema_version")
    if data["kind"] != CANDIDATE_EVIDENCE_KIND:
        raise ValueError("CandidateEvidence kind is invalid")
    value = CandidateEvidence(
        evidence_id=_identifier(data["evidence_id"], "evidence_id"),
        arena_id=_identifier(data["arena_id"], "arena_id"),
        candidate_id=_identifier(data["candidate_id"], "candidate_id"),
        ordinal=_ordinal(data["ordinal"]),
        owner_id=_identifier(data["owner_id"], "owner_id"),
        workspace_id=_identifier(data["workspace_id"], "workspace_id"),
        base_revision=_identifier(data["base_revision"], "base_revision"),
        run_id=_identifier(data["run_id"], "run_id"),
        session_id=_identifier(data["session_id"], "session_id"),
        status=_enum(CandidateStatus, data["status"], "status"),
        isolation=_isolation_from_dict(data["isolation"]),
        run_capsule=_binding_from_dict(data["run_capsule"], "run_capsule"),
        context_manifest=_binding_from_dict(
            data["context_manifest"], "context_manifest"
        ),
        change_set=_binding_from_dict(data["change_set"], "change_set"),
        cost_lease_id=_identifier(data["cost_lease_id"], "cost_lease_id"),
        cost_receipt=_binding_from_dict(data["cost_receipt"], "cost_receipt"),
        gate=_gate_from_dict(data["gate"]),
        created_at=_timestamp(data["created_at"], "created_at"),
        evidence_sha256=_hash(data["evidence_sha256"], "evidence_sha256"),
    )
    _verify_candidate(value)
    return value


def arbitration_receipt_to_dict(value: ArbitrationReceipt) -> dict[str, object]:
    """Serialize one arbitration receipt."""
    return {
        "schema_version": REVIEWED_ARENA_SCHEMA_VERSION,
        "kind": ARBITRATION_RECEIPT_KIND,
        "receipt_id": value.receipt_id,
        "arena_id": value.arena_id,
        "owner_id": value.owner_id,
        "workspace_id": value.workspace_id,
        "base_revision": value.base_revision,
        "outcome": value.outcome.value,
        "candidates": [_candidate_digest_to_dict(item) for item in value.candidates],
        "selected_candidate_id": value.selected_candidate_id,
        "reviewer_evidence": (
            _binding_to_dict(value.reviewer_evidence)
            if value.reviewer_evidence is not None
            else None
        ),
        "reason_code": value.reason_code,
        "created_at": value.created_at,
        "automatic_apply": value.automatic_apply,
        "receipt_sha256": value.receipt_sha256,
    }


def arbitration_receipt_from_dict(payload: object) -> ArbitrationReceipt:
    """Parse and verify one arbitration receipt."""
    data = _mapping(payload, "ArbitrationReceipt")
    _exact_fields(
        data,
        {
            "schema_version",
            "kind",
            "receipt_id",
            "arena_id",
            "owner_id",
            "workspace_id",
            "base_revision",
            "outcome",
            "candidates",
            "selected_candidate_id",
            "reviewer_evidence",
            "reason_code",
            "created_at",
            "automatic_apply",
            "receipt_sha256",
        },
        "ArbitrationReceipt",
    )
    if data["schema_version"] != REVIEWED_ARENA_SCHEMA_VERSION:
        raise ValueError("unsupported ArbitrationReceipt schema_version")
    if data["kind"] != ARBITRATION_RECEIPT_KIND:
        raise ValueError("ArbitrationReceipt kind is invalid")
    raw_candidates = data["candidates"]
    if not isinstance(raw_candidates, list) or len(raw_candidates) != 2:
        raise ValueError("ArbitrationReceipt requires exactly two candidates")
    candidates = tuple(_candidate_digest_from_dict(item) for item in raw_candidates)
    if len(candidates) != 2:
        raise ValueError("ArbitrationReceipt requires exactly two candidates")
    reviewer_payload = data["reviewer_evidence"]
    value = ArbitrationReceipt(
        receipt_id=_identifier(data["receipt_id"], "receipt_id"),
        arena_id=_identifier(data["arena_id"], "arena_id"),
        owner_id=_identifier(data["owner_id"], "owner_id"),
        workspace_id=_identifier(data["workspace_id"], "workspace_id"),
        base_revision=_identifier(data["base_revision"], "base_revision"),
        outcome=_enum(ArenaOutcome, data["outcome"], "outcome"),
        candidates=(candidates[0], candidates[1]),
        selected_candidate_id=_optional_identifier(
            data["selected_candidate_id"], "selected_candidate_id"
        ),
        reviewer_evidence=(
            None
            if reviewer_payload is None
            else _binding_from_dict(reviewer_payload, "reviewer_evidence")
        ),
        reason_code=_identifier(data["reason_code"], "reason_code"),
        created_at=_timestamp(data["created_at"], "created_at"),
        automatic_apply=data["automatic_apply"],
        receipt_sha256=_hash(data["receipt_sha256"], "receipt_sha256"),
    )
    _verify_arbitration(value)
    return value


def canonical_sha256(payload: Mapping[str, object]) -> str:
    """Hash one JSON-compatible mapping with the Arena canonical encoding."""
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_candidate(value: CandidateEvidence) -> None:
    if value.evidence_id != f"arena_candidate_{value.evidence_sha256[:24]}":
        raise ValueError("CandidateEvidence evidence_id does not match digest")
    body = candidate_evidence_to_dict(
        replace(value, evidence_id="", evidence_sha256="")
    )
    body.pop("evidence_id")
    body.pop("evidence_sha256")
    if canonical_sha256(body) != value.evidence_sha256:
        raise ValueError("CandidateEvidence evidence_sha256 does not match")


def _verify_arbitration(value: ArbitrationReceipt) -> None:
    if value.automatic_apply is not False:
        raise ValueError("Reviewed Arena automatic_apply must be false")
    if tuple(item.ordinal for item in value.candidates) != (1, 2):
        raise ValueError("ArbitrationReceipt candidates must use ordinals 1 and 2")
    candidate_ids = {item.candidate_id for item in value.candidates}
    if len(candidate_ids) != 2:
        raise ValueError("ArbitrationReceipt candidate ids must be distinct")
    if len({item.evidence_sha256 for item in value.candidates}) != 2:
        raise ValueError("ArbitrationReceipt evidence digests must be distinct")
    if value.outcome is ArenaOutcome.SELECTED:
        if value.selected_candidate_id not in candidate_ids:
            raise ValueError("selected outcome requires one bound candidate")
        if value.reviewer_evidence is None:
            raise ValueError("selected outcome requires reviewer evidence")
    elif value.selected_candidate_id is not None:
        raise ValueError("non-selected outcome cannot carry a selected candidate")
    if value.receipt_id != f"arena_receipt_{value.receipt_sha256[:24]}":
        raise ValueError("ArbitrationReceipt receipt_id does not match digest")
    body = arbitration_receipt_to_dict(replace(value, receipt_id="", receipt_sha256=""))
    body.pop("receipt_id")
    body.pop("receipt_sha256")
    if canonical_sha256(body) != value.receipt_sha256:
        raise ValueError("ArbitrationReceipt receipt_sha256 does not match")


def _binding_to_dict(value: EvidenceBinding) -> dict[str, str]:
    return {
        "authority": value.authority,
        "resource_id": value.resource_id,
        "revision": value.revision,
        "sha256": value.sha256,
    }


def _binding_from_dict(payload: object, field: str) -> EvidenceBinding:
    data = _mapping(payload, field)
    _exact_fields(data, {"authority", "resource_id", "revision", "sha256"}, field)
    return EvidenceBinding(
        authority=_identifier(data["authority"], f"{field}.authority"),
        resource_id=_identifier(data["resource_id"], f"{field}.resource_id"),
        revision=_identifier(data["revision"], f"{field}.revision"),
        sha256=_hash(data["sha256"], f"{field}.sha256"),
    )


def _isolation_to_dict(value: CandidateIsolation) -> dict[str, str]:
    return {
        "worktree_id": value.worktree_id,
        "native_home_id": value.native_home_id,
        "terminal_id": value.terminal_id,
        "provider_session_id": value.provider_session_id,
    }


def _isolation_from_dict(payload: object) -> CandidateIsolation:
    data = _mapping(payload, "isolation")
    fields = {
        "worktree_id",
        "native_home_id",
        "terminal_id",
        "provider_session_id",
    }
    _exact_fields(data, fields, "isolation")
    return CandidateIsolation(
        worktree_id=_identifier(data["worktree_id"], "isolation.worktree_id"),
        native_home_id=_identifier(data["native_home_id"], "isolation.native_home_id"),
        terminal_id=_identifier(data["terminal_id"], "isolation.terminal_id"),
        provider_session_id=_identifier(
            data["provider_session_id"], "isolation.provider_session_id"
        ),
    )


def _gate_to_dict(value: DeterministicGateReceipt) -> dict[str, str]:
    return {
        "gate_id": value.gate_id,
        "command_sha256": value.command_sha256,
        "result_sha256": value.result_sha256,
        "checked_revision": value.checked_revision,
        "outcome": value.outcome.value,
        "completed_at": value.completed_at,
    }


def _gate_from_dict(payload: object) -> DeterministicGateReceipt:
    data = _mapping(payload, "gate")
    _exact_fields(
        data,
        {
            "gate_id",
            "command_sha256",
            "result_sha256",
            "checked_revision",
            "outcome",
            "completed_at",
        },
        "gate",
    )
    return DeterministicGateReceipt(
        gate_id=_identifier(data["gate_id"], "gate.gate_id"),
        command_sha256=_hash(data["command_sha256"], "gate.command_sha256"),
        result_sha256=_hash(data["result_sha256"], "gate.result_sha256"),
        checked_revision=_identifier(data["checked_revision"], "gate.checked_revision"),
        outcome=_enum(GateOutcome, data["outcome"], "gate.outcome"),
        completed_at=_timestamp(data["completed_at"], "gate.completed_at"),
    )


def _candidate_digest_to_dict(value: CandidateEvidenceDigest) -> dict[str, object]:
    return {
        "candidate_id": value.candidate_id,
        "ordinal": value.ordinal,
        "evidence_sha256": value.evidence_sha256,
    }


def _candidate_digest_from_dict(payload: object) -> CandidateEvidenceDigest:
    data = _mapping(payload, "candidate")
    _exact_fields(data, {"candidate_id", "ordinal", "evidence_sha256"}, "candidate")
    return CandidateEvidenceDigest(
        candidate_id=_identifier(data["candidate_id"], "candidate.candidate_id"),
        ordinal=_ordinal(data["ordinal"]),
        evidence_sha256=_hash(data["evidence_sha256"], "candidate.evidence_sha256"),
    )


def _mapping(payload: object, field: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], payload)


def _exact_fields(
    data: Mapping[str, Any],
    expected: set[str],
    field: str,
) -> None:
    if set(data) != expected:
        raise ValueError(f"{field} has unknown or missing fields")


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def _optional_identifier(value: object, field: str) -> str | None:
    return None if value is None else _identifier(value, field)


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase sha256 digest")
    return value


def _ordinal(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value not in {1, 2}:
        raise ValueError("candidate ordinal must be 1 or 2")
    return value


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a UTC timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a UTC timestamp")
    return value


def _enum(enum_type: type[Any], value: object, field: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc


__all__ = [
    "arbitration_receipt_from_dict",
    "arbitration_receipt_to_dict",
    "candidate_evidence_from_dict",
    "candidate_evidence_to_dict",
    "canonical_sha256",
]
