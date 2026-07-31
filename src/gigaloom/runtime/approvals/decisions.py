"""Apply approval decisions and consume grants atomically."""

from __future__ import annotations

from typing import Any

from gigaloom.runtime.db.transactions import transaction as _transaction
from gigaloom.runtime.models import (
    ApprovalStatus,
    JobStatus,
)
from gigaloom.runtime.policy import (
    ApprovalDecision,
    ApprovalGrant,
    ApprovalRequest,
    EnforcementLevel,
    PermissionAction,
    PolicyAuditEvent,
    PolicyAuditPhase,
    PolicyDecision,
    approval_binding_digest,
)
from gigaloom.runtime.repositories.base import RuntimeRepository
from gigaloom.runtime.repositories.errors import (
    ConcurrentUpdateError,
    InvalidStateTransitionError,
)
from gigaloom.runtime.repositories.records import (
    _approval_grant_from_row,
    _approval_once_scope,
    _approval_preview_binding,
    _approval_preview_binding_json,
    _approval_request_from_row,
    _append_policy_audit_event,
    _expire_approvals,
    _future_time,
    _job_from_row,
    _new_id,
    _optional_text,
    _policy_audit_event_from_row,
    _required_text,
    _utc_now,
)


class ApprovalDecisionsRepository(RuntimeRepository):
    """Apply approval decisions and consume grants atomically."""

    def decide_approval_request(
        self,
        request_id: str,
        decision: ApprovalDecision | str,
        *,
        project_expiry_seconds: float | None = None,
    ) -> ApprovalRequest:
        """Persist a decision, optional scoped grant, and pre-spawn job outcome."""
        parsed_decision = ApprovalDecision(decision)
        now = _utc_now()
        wake_worker = False
        with self._connect() as connection, _transaction(connection):
            _expire_approvals(connection, now)
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            request = _approval_request_from_row(row)
            if request.status is not ApprovalStatus.PENDING:
                raise InvalidStateTransitionError(
                    f"approval {request_id} is {request.status.value}"
                )
            if _approval_preview_binding(
                request.preview
            ) is not None and parsed_decision not in {
                ApprovalDecision.ALLOW_ONCE,
                ApprovalDecision.DENY,
            }:
                raise ValueError(
                    "Hash-bound approvals can only be allowed once or denied"
                )
            grant: tuple[str, str, int | None, str | None] | None = None
            if parsed_decision is ApprovalDecision.ALLOW_ONCE:
                scope_type, scope_id = _approval_once_scope(request)
                grant = (scope_type, scope_id, 1, None)
            elif parsed_decision is ApprovalDecision.ALLOW_RUN:
                if not request.run_id:
                    raise ValueError("run-scoped approval requires a run")
                grant = ("run", request.run_id, None, None)
            elif parsed_decision is ApprovalDecision.ALLOW_SESSION:
                if not request.session_id:
                    raise ValueError("session-scoped approval requires a session")
                grant = ("session", request.session_id, None, None)
            elif parsed_decision is ApprovalDecision.ALLOW_PROJECT:
                if not request.project_id:
                    raise ValueError("project-scoped approval requires a project")
                if project_expiry_seconds is None or project_expiry_seconds <= 0:
                    raise ValueError(
                        "project-scoped approval requires a positive expiry"
                    )
                grant = (
                    "project",
                    request.project_id,
                    None,
                    _future_time(project_expiry_seconds),
                )
            status = (
                ApprovalStatus.DENIED
                if parsed_decision is ApprovalDecision.DENY
                else ApprovalStatus.APPROVED
            )
            connection.execute(
                """
                UPDATE approval_requests
                SET status = ?, decision = ?, decided_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status.value,
                    parsed_decision.value,
                    now,
                    request_id,
                    ApprovalStatus.PENDING.value,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentUpdateError(
                    f"approval {request_id} changed concurrently"
                )
            grant_id: str | None = None
            if grant is not None:
                scope_type, scope_id, uses_remaining, expires_at = grant
                grant_id = _new_id("grant")
                connection.execute(
                    """
                    INSERT INTO approval_grants (
                        id, request_id, action, scope_type, scope_id,
                        uses_remaining, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        grant_id,
                        request_id,
                        request.action.value,
                        scope_type,
                        scope_id,
                        uses_remaining,
                        expires_at,
                        now,
                    ),
                )
            if request.enforcement_owner:
                _append_policy_audit_event(
                    connection,
                    operation_id=request.id,
                    action=request.action,
                    phase=PolicyAuditPhase.DECISION,
                    decision=parsed_decision.value,
                    enforcement=request.enforcement,
                    enforcement_owner=request.enforcement_owner,
                    policy_source=request.policy_source,
                    approval_request_id=request.id,
                    approval_grant_id=grant_id,
                    approval_binding_sha256=_approval_preview_binding(request.preview),
                    project_id=request.project_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    job_id=request.job_id,
                    evidence={"approval_status": status.value},
                    created_at=now,
                )
            if request.job_id:
                job_row = connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (request.job_id,)
                ).fetchone()
                if job_row is not None:
                    job = _job_from_row(job_row)
                    if job.status is JobStatus.WAITING_APPROVAL:
                        target = (
                            JobStatus.CANCELED
                            if parsed_decision is ApprovalDecision.DENY
                            else JobStatus.QUEUED
                        )
                        wake_worker = target is JobStatus.QUEUED
                        next_version = job.version + 1
                        connection.execute(
                            """
                            UPDATE jobs SET status = ?, available_at = ?,
                                terminal_at = ?, error_summary = ?, updated_at = ?,
                                approval_request_id = NULL, version = ?
                            WHERE id = ? AND version = ?
                            """,
                            (
                                target.value,
                                now,
                                now if target is JobStatus.CANCELED else None,
                                "approval denied"
                                if target is JobStatus.CANCELED
                                else None,
                                now,
                                next_version,
                                job.id,
                                job.version,
                            ),
                        )
                        if target is JobStatus.CANCELED:
                            self._enqueue_terminal_sync(
                                connection,
                                job_id=job.id,
                                status=target,
                                version=next_version,
                                session_id=job.session_id,
                                attempt=None,
                            )
            updated = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
        result = _approval_request_from_row(updated)
        if wake_worker:
            self.wake_workers()
        return result

    def consume_matching_approval_grant(
        self,
        *,
        action: PermissionAction | str,
        project_id: str | None,
        run_id: str | None,
        job_id: str | None,
        session_id: str | None = None,
        approval_binding: str | None = None,
        enforcement_owner: str | None = None,
    ) -> bool:
        """Consume a matching allow-once grant or observe a scoped grant."""
        parsed_action = PermissionAction(action)
        scopes = [
            ("job", _optional_text(job_id)),
            ("run", _optional_text(run_id)),
            ("session", _optional_text(session_id)),
            ("project", _optional_text(project_id)),
        ]
        scopes = [(kind, value) for kind, value in scopes if value]
        if not scopes:
            return False
        clauses = " OR ".join("(scope_type = ? AND scope_id = ?)" for _ in scopes)
        params: list[Any] = [parsed_action.value]
        for kind, value in scopes:
            params.extend((kind, value))
        now = _utc_now()
        params.extend((now,))
        binding_hash = (
            approval_binding_digest(approval_binding) if approval_binding else None
        )
        with self._connect() as connection, _transaction(connection):
            rows = connection.execute(
                f"""
                SELECT approval_grants.*,
                       approval_requests.preview_json,
                       approval_requests.policy_source AS request_policy_source,
                       approval_requests.enforcement AS request_enforcement,
                       approval_requests.enforcement_owner AS request_enforcement_owner,
                       approval_requests.project_id AS request_project_id,
                       approval_requests.session_id AS request_session_id,
                       approval_requests.run_id AS request_run_id,
                       approval_requests.job_id AS request_job_id
                FROM approval_grants
                JOIN approval_requests
                  ON approval_requests.id = approval_grants.request_id
                WHERE approval_grants.action = ? AND ({clauses})
                  AND (approval_grants.expires_at IS NULL OR approval_grants.expires_at > ?)
                  AND (approval_grants.uses_remaining IS NULL OR approval_grants.uses_remaining > 0)
                ORDER BY CASE approval_grants.scope_type
                             WHEN 'job' THEN 0
                             WHEN 'run' THEN 1
                             WHEN 'session' THEN 2
                             ELSE 3 END,
                         approval_grants.created_at DESC
                """,
                tuple(params),
            ).fetchall()
            row = next(
                (
                    candidate
                    for candidate in rows
                    if _approval_preview_binding_json(candidate["preview_json"])
                    == binding_hash
                    and _optional_text(candidate["request_enforcement_owner"])
                    == _optional_text(enforcement_owner)
                ),
                None,
            )
            if row is None:
                return False
            if row["uses_remaining"] is not None:
                connection.execute(
                    """
                    UPDATE approval_grants SET uses_remaining = uses_remaining - 1
                    WHERE id = ? AND uses_remaining > 0
                    """,
                    (row["id"],),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    return False
            if enforcement_owner:
                _append_policy_audit_event(
                    connection,
                    operation_id=str(row["request_id"]),
                    action=parsed_action,
                    phase=PolicyAuditPhase.ENFORCEMENT,
                    decision=PolicyDecision.ALLOW.value,
                    enforcement=EnforcementLevel(str(row["request_enforcement"])),
                    enforcement_owner=enforcement_owner,
                    policy_source=str(row["request_policy_source"]),
                    approval_request_id=str(row["request_id"]),
                    approval_grant_id=str(row["id"]),
                    approval_binding_sha256=binding_hash,
                    project_id=_optional_text(row["request_project_id"]),
                    session_id=_optional_text(row["request_session_id"]),
                    run_id=_optional_text(row["request_run_id"]),
                    job_id=_optional_text(row["request_job_id"]),
                    evidence={
                        "scope_type": str(row["scope_type"]),
                        "scope_id": str(row["scope_id"]),
                    },
                    created_at=now,
                )
        return True

    def list_approval_grants(self) -> tuple[ApprovalGrant, ...]:
        """List grants for safe runtime inspection and export."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approval_grants ORDER BY created_at, id"
            ).fetchall()
        return tuple(_approval_grant_from_row(row) for row in rows)

    def list_policy_audit_events(
        self,
        *,
        operation_id: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
    ) -> tuple[PolicyAuditEvent, ...]:
        """List immutable policy evidence in operation order."""
        page_size = max(1, min(int(limit), 1000))
        filters: list[str] = []
        values: list[Any] = []
        if operation_id is not None:
            filters.append("operation_id = ?")
            values.append(_required_text(operation_id, "operation_id"))
        if run_id is not None:
            filters.append("run_id = ?")
            values.append(_required_text(run_id, "run_id"))
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        order = (
            "sequence"
            if operation_id is not None
            else "created_at, operation_id, sequence"
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM policy_audit_events {where} ORDER BY {order} LIMIT ?",
                (*values, page_size),
            ).fetchall()
        return tuple(_policy_audit_event_from_row(row) for row in rows)
