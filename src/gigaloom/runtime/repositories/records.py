"""SQLite record codecs and persistence value normalization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from gigaloom.runtime.models import (
    ApprovalStatus,
    JobAttempt,
    NativeProcessOutputRecord,
    NativeProcessRecord,
    RuntimeJob,
    RuntimeOutboxEntry,
    RuntimeWorker,
    SideEffectRecord,
    SideEffectStatus,
    parse_attempt_status,
    parse_job_status,
)
from gigaloom.runtime.policy import (
    ApprovalDecision,
    ApprovalGrant,
    ApprovalRequest,
    EnforcementLevel,
    PermissionAction,
    PolicyAuditEvent,
    PolicyAuditPhase,
)
from gigaloom.sessions.contracts import redact_for_storage


def _job_from_row(row: sqlite3.Row) -> RuntimeJob:
    return RuntimeJob(
        id=str(row["id"]),
        origin=str(row["origin"]),
        idempotency_key_hash=str(row["idempotency_key_hash"]),
        status=parse_job_status(row["status"]),
        session_id=str(row["session_id"]),
        user_message_id=str(row["user_message_id"]),
        initial_run_id=(
            str(row["initial_run_id"])
            if row["initial_run_id"] is not None
            else f"run_legacy_{row['id']}"
        ),
        project_id=_optional_text(row["project_id"]),
        workflow_id=_optional_text(row["workflow_id"]),
        workflow_version=_optional_text(row["workflow_version"]),
        schedule_id=_optional_text(row["schedule_id"]),
        agent_id=_optional_text(row["agent_id"]),
        available_at=_optional_text(row["available_at"]),
        terminal_at=_optional_text(row["terminal_at"]),
        cancel_requested_at=_optional_text(row["cancel_requested_at"]),
        max_attempts=int(row["max_attempts"]),
        priority=int(row["priority"]),
        version=int(row["version"]),
        error_summary=_optional_text(row["error_summary"]),
        required_harness_id=_optional_text(row["required_harness_id"]),
        required_capability_fingerprint=_json_mapping(row["required_fingerprint_json"]),
        timeout_seconds=(
            float(row["timeout_seconds"])
            if row["timeout_seconds"] is not None
            else None
        ),
        approval_request_id=_optional_text(row["approval_request_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _attempt_from_row(row: sqlite3.Row) -> JobAttempt:
    return JobAttempt(
        id=str(row["id"]),
        job_id=str(row["job_id"]),
        attempt_number=int(row["attempt_number"]),
        status=parse_attempt_status(row["status"]),
        run_id=str(row["run_id"]),
        lease_owner=_optional_text(row["lease_owner"]),
        leased_until=_optional_text(row["leased_until"]),
        heartbeat_at=_optional_text(row["heartbeat_at"]),
        started_at=_optional_text(row["started_at"]),
        finished_at=_optional_text(row["finished_at"]),
        process_id=int(row["process_id"]) if row["process_id"] is not None else None,
        process_group_id=(
            int(row["process_group_id"])
            if row["process_group_id"] is not None
            else None
        ),
        retry_reason=_optional_text(row["retry_reason"]),
        idempotency_class=str(row["idempotency_class"]),
        error_summary=_optional_text(row["error_summary"]),
        capability_fingerprint=_json_mapping(row["capability_fingerprint_json"]),
        version=int(row["version"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _side_effect_from_row(row: sqlite3.Row) -> SideEffectRecord:
    return SideEffectRecord(
        id=str(row["id"]),
        job_id=str(row["job_id"]),
        token_hash=str(row["token_hash"]),
        operation=str(row["operation"]),
        intent_hash=str(row["intent_hash"]),
        status=SideEffectStatus(str(row["status"])),
        owner_attempt_id=str(row["owner_attempt_id"]),
        completion_evidence=_json_mapping(row["completion_evidence_json"]),
        completion_evidence_hash=_optional_text(row["completion_evidence_hash"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=_optional_text(row["completed_at"]),
    )


def _outbox_from_row(row: sqlite3.Row) -> RuntimeOutboxEntry:
    payload = json.loads(str(row["payload_json"]))
    return RuntimeOutboxEntry(
        id=str(row["id"]),
        aggregate_type=str(row["aggregate_type"]),
        aggregate_id=str(row["aggregate_id"]),
        event_type=str(row["event_type"]),
        dedupe_key=str(row["dedupe_key"]),
        payload=dict(payload) if isinstance(payload, Mapping) else {},
        created_at=str(row["created_at"]),
        processed_at=_optional_text(row["processed_at"]),
        attempt_count=int(row["attempt_count"]),
        last_error=_optional_text(row["last_error"]),
    )


def _worker_from_row(row: sqlite3.Row) -> RuntimeWorker:
    return RuntimeWorker(
        id=str(row["id"]),
        process_id=int(row["process_id"]),
        hostname=str(row["hostname"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        stopped_at=_optional_text(row["stopped_at"]),
        capability_fingerprint=_json_mapping(row["capability_fingerprint_json"]),
    )


def _native_process_from_row(row: sqlite3.Row) -> NativeProcessRecord:
    return NativeProcessRecord(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        owner_process_id=int(row["owner_process_id"]),
        session_id=str(row["session_id"]),
        run_id=str(row["run_id"]),
        harness_id=str(row["harness_id"]),
        status=str(row["status"]),
        process_id=(int(row["process_id"]) if row["process_id"] is not None else None),
        process_group_id=(
            int(row["process_group_id"])
            if row["process_group_id"] is not None
            else None
        ),
        transport=str(row["transport"]),
        ref=_json_mapping(row["ref_json"]),
        started_at=str(row["started_at"]),
        updated_at=str(row["updated_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        leased_until=str(row["leased_until"]),
        timeout_at=_optional_text(row["timeout_at"]),
        cancel_requested_at=_optional_text(row["cancel_requested_at"]),
        finished_at=_optional_text(row["finished_at"]),
        terminal_cursor=int(row["terminal_cursor"]),
        recovery_outcome=_optional_text(row["recovery_outcome"]),
        version=int(row["version"]),
    )


def _native_process_output_from_row(
    row: sqlite3.Row,
) -> NativeProcessOutputRecord:
    return NativeProcessOutputRecord(
        process_id=str(row["process_id"]),
        cursor=int(row["cursor"]),
        stream=str(row["stream"]),
        text=str(redact_for_storage(row["text"])),
        created_at=str(row["created_at"]),
    )


def _approval_request_from_row(row: sqlite3.Row) -> ApprovalRequest:
    preview = json.loads(str(row["preview_json"]))
    return ApprovalRequest(
        id=str(row["id"]),
        action=PermissionAction(str(row["action"])),
        status=ApprovalStatus(str(row["status"])),
        enforcement=EnforcementLevel(str(row["enforcement"])),
        policy_source=str(row["policy_source"]),
        enforcement_owner=_optional_text(row["enforcement_owner"]),
        reason=str(row["reason"]),
        preview=dict(preview) if isinstance(preview, Mapping) else {},
        project_id=_optional_text(row["project_id"]),
        session_id=_optional_text(row["session_id"]),
        run_id=_optional_text(row["run_id"]),
        job_id=_optional_text(row["job_id"]),
        decision=(
            ApprovalDecision(str(row["decision"]))
            if row["decision"] is not None
            else None
        ),
        expires_at=_optional_text(row["expires_at"]),
        decided_at=_optional_text(row["decided_at"]),
        created_at=str(row["created_at"]),
    )


def _approval_grant_from_row(row: sqlite3.Row) -> ApprovalGrant:
    return ApprovalGrant(
        id=str(row["id"]),
        request_id=str(row["request_id"]),
        action=PermissionAction(str(row["action"])),
        scope_type=str(row["scope_type"]),
        scope_id=str(row["scope_id"]),
        uses_remaining=(
            int(row["uses_remaining"]) if row["uses_remaining"] is not None else None
        ),
        expires_at=_optional_text(row["expires_at"]),
        created_at=str(row["created_at"]),
    )


def _policy_audit_event_from_row(row: sqlite3.Row) -> PolicyAuditEvent:
    evidence = _json_mapping(row["evidence_json"])
    return PolicyAuditEvent(
        id=str(row["id"]),
        operation_id=str(row["operation_id"]),
        sequence=int(row["sequence"]),
        action=PermissionAction(str(row["action"])),
        phase=PolicyAuditPhase(str(row["phase"])),
        decision=str(row["decision"]),
        enforcement=EnforcementLevel(str(row["enforcement"])),
        enforcement_owner=str(row["enforcement_owner"]),
        policy_source=str(row["policy_source"]),
        approval_request_id=str(row["approval_request_id"]),
        approval_grant_id=_optional_text(row["approval_grant_id"]),
        approval_binding_sha256=_optional_text(row["approval_binding_sha256"]),
        project_id=_optional_text(row["project_id"]),
        session_id=_optional_text(row["session_id"]),
        run_id=_optional_text(row["run_id"]),
        job_id=_optional_text(row["job_id"]),
        evidence=evidence,
        previous_event_sha256=_optional_text(row["previous_event_sha256"]),
        event_sha256=str(row["event_sha256"]),
        created_at=str(row["created_at"]),
    )


def _approval_once_scope(request: ApprovalRequest) -> tuple[str, str]:
    if request.job_id:
        return "job", request.job_id
    if request.run_id:
        return "run", request.run_id
    if request.project_id:
        return "project", request.project_id
    raise ValueError("allow-once approval requires a job, run, or project scope")


def _expire_approvals(connection: sqlite3.Connection, now: str) -> None:
    connection.execute(
        """
        UPDATE approval_requests SET status = ?
        WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?
        """,
        (ApprovalStatus.EXPIRED.value, ApprovalStatus.PENDING.value, now),
    )


def _idempotency_hash(value: str) -> str:
    text = _required_text(value, "idempotency_key")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _opaque_token_hash(value: str) -> str:
    text = _required_text(value, "side_effect_token")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _approval_preview_binding(preview: Mapping[str, Any]) -> str | None:
    value = preview.get("approval_binding_sha256")
    if value is None or not str(value).strip():
        return None
    return str(value)


def _approval_preview_binding_json(value: Any) -> str | None:
    try:
        preview = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    if not isinstance(preview, Mapping):
        return None
    return _approval_preview_binding(preview)


def _append_policy_audit_event(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    action: PermissionAction,
    phase: PolicyAuditPhase,
    decision: str,
    enforcement: EnforcementLevel,
    enforcement_owner: str,
    policy_source: str,
    approval_request_id: str,
    approval_grant_id: str | None = None,
    approval_binding_sha256: str | None = None,
    project_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    evidence: Mapping[str, Any] | None = None,
    created_at: str,
) -> None:
    """Append one immutable, hash-chained policy operation event."""
    operation = _required_text(operation_id, "policy operation_id")
    previous = connection.execute(
        """
        SELECT sequence, event_sha256 FROM policy_audit_events
        WHERE operation_id = ? ORDER BY sequence DESC LIMIT 1
        """,
        (operation,),
    ).fetchone()
    sequence = int(previous["sequence"]) + 1 if previous is not None else 1
    previous_hash = str(previous["event_sha256"]) if previous is not None else None
    event_id = _new_id("policy_audit")
    safe_evidence = _safe_mapping(evidence or {}, "policy audit evidence")
    payload = {
        "action": action.value,
        "approval_binding_sha256": _optional_text(approval_binding_sha256),
        "approval_grant_id": _optional_text(approval_grant_id),
        "approval_request_id": _required_text(
            approval_request_id, "approval_request_id"
        ),
        "created_at": created_at,
        "decision": _required_text(decision, "policy decision"),
        "enforcement": enforcement.value,
        "enforcement_owner": _required_text(enforcement_owner, "enforcement_owner"),
        "evidence": safe_evidence,
        "id": event_id,
        "job_id": _optional_text(job_id),
        "operation_id": operation,
        "phase": phase.value,
        "policy_source": _required_text(policy_source, "policy_source"),
        "previous_event_sha256": previous_hash,
        "project_id": _optional_text(project_id),
        "run_id": _optional_text(run_id),
        "sequence": sequence,
        "session_id": _optional_text(session_id),
    }
    event_hash = hashlib.sha256(
        _canonical_json(payload, "policy audit event").encode("utf-8")
    ).hexdigest()
    connection.execute(
        """
        INSERT INTO policy_audit_events (
            id, operation_id, sequence, action, phase, decision, enforcement,
            enforcement_owner, policy_source, approval_request_id,
            approval_grant_id, approval_binding_sha256, project_id, session_id,
            run_id, job_id, evidence_json, previous_event_sha256, event_sha256,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            operation,
            sequence,
            action.value,
            phase.value,
            payload["decision"],
            enforcement.value,
            payload["enforcement_owner"],
            payload["policy_source"],
            payload["approval_request_id"],
            payload["approval_grant_id"],
            payload["approval_binding_sha256"],
            payload["project_id"],
            payload["session_id"],
            payload["run_id"],
            payload["job_id"],
            _canonical_json(safe_evidence, "policy audit evidence"),
            previous_hash,
            event_hash,
            created_at,
        ),
    )


def _canonical_json(value: Mapping[str, Any], name: str) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-serializable") from exc


def _mapping_hash(value: Mapping[str, Any], name: str) -> str:
    return hashlib.sha256(_canonical_json(value, name).encode("utf-8")).hexdigest()


def _safe_mapping(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    safe = redact_for_storage(dict(value))
    if not isinstance(safe, Mapping):
        raise ValueError(f"{name} must be a mapping")
    result = dict(safe)
    _canonical_json(result, name)
    return result


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_optional_text(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    safe = redact_for_storage(text)
    return str(safe)[:4000]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_time(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _safe_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        redact_for_storage(dict(value)), ensure_ascii=False, sort_keys=True
    )


def _json_mapping(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _safe_workflow_export_row(row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key.endswith("_json"):
            try:
                value = json.loads(str(value))
            except (TypeError, ValueError):
                value = {}
        payload[str(key)] = redact_for_storage(value)
    return payload


def _fingerprint_matches(
    required: Mapping[str, Any],
    worker: Mapping[str, Any],
    *,
    required_harness_id: str | None,
) -> bool:
    worker_harnesses = worker.get("harnesses")
    if not isinstance(worker_harnesses, Mapping):
        return required_harness_id is None and not required
    if required_harness_id is not None:
        worker_harness = worker_harnesses.get(required_harness_id)
        if not isinstance(worker_harness, Mapping) or not bool(
            worker_harness.get("available")
        ):
            return False
    required_os = _optional_text(required.get("os"))
    if required_os is not None and required_os != _optional_text(worker.get("os")):
        return False
    required_harnesses = required.get("harnesses")
    if not isinstance(required_harnesses, Mapping):
        return True
    for harness_id, expected in required_harnesses.items():
        actual = worker_harnesses.get(str(harness_id))
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            return False
        for key in (
            "distribution",
            "binary_version",
            "structured_capability_hash",
        ):
            value = expected.get(key)
            if value is not None and value != actual.get(key):
                return False
        expected_features = expected.get("features")
        actual_features = actual.get("features")
        if isinstance(expected_features, Mapping):
            if not isinstance(actual_features, Mapping):
                return False
            if any(
                bool(value) and not bool(actual_features.get(str(key)))
                for key, value in expected_features.items()
            ):
                return False
    return True


def _retry_safe(value: str) -> bool:
    return value in {
        "read_only",
        "safe_retry",
        "deterministic",
        "structured_recoverable",
    }
