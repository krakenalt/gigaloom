"""Store approval requests and attention state."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from gigaloom.runtime.db.transactions import transaction as _transaction
from gigaloom.runtime.models import (
    ApprovalStatus,
)
from gigaloom.runtime.policy import (
    ApprovalRequest,
    PolicyAuditPhase,
    PolicyContext,
    PolicyResolution,
    approval_binding_digest,
    redacted_policy_preview,
)
from gigaloom.runtime.repositories.base import RuntimeRepository
from gigaloom.runtime.repositories.records import (
    _approval_preview_binding,
    _approval_request_from_row,
    _append_policy_audit_event,
    _expire_approvals,
    _mapping_hash,
    _new_id,
    _optional_text,
    _required_text,
    _utc_now,
)


class ApprovalsRepository(RuntimeRepository):
    """Store approval requests and attention state."""

    def create_approval_request(
        self,
        resolution: PolicyResolution,
        context: PolicyContext,
        *,
        expires_at: str | None = None,
    ) -> ApprovalRequest:
        """Create or return one pending request for the same action and scope."""
        if resolution.decision.value != "ask":
            raise ValueError("approval requests require an ask policy resolution")
        now = _utc_now()
        request_id = _new_id("approval")
        scope_identity = "\0".join(
            (
                resolution.action.value,
                context.project_id or "",
                context.session_id or "",
                context.run_id or "",
                context.job_id or "",
                context.approval_binding or "",
                context.enforcement_owner or "",
            )
        )
        dedupe_key = hashlib.sha256(scope_identity.encode("utf-8")).hexdigest()
        preview = redacted_policy_preview(context.preview)
        if context.approval_binding:
            preview["approval_binding_sha256"] = approval_binding_digest(
                context.approval_binding
            )
        values = (
            request_id,
            resolution.action.value,
            ApprovalStatus.PENDING.value,
            resolution.enforcement.value,
            resolution.policy_source,
            _optional_text(context.enforcement_owner),
            _required_text(context.reason, "approval reason"),
            json.dumps(
                preview,
                ensure_ascii=False,
                sort_keys=True,
            ),
            _optional_text(context.project_id),
            _optional_text(context.session_id),
            _optional_text(context.run_id),
            _optional_text(context.job_id),
            dedupe_key,
            expires_at,
            now,
        )
        with self._connect() as connection, _transaction(connection):
            _expire_approvals(connection, now)
            try:
                connection.execute(
                    """
                    INSERT INTO approval_requests (
                        id, action, status, enforcement, policy_source,
                        enforcement_owner, reason, preview_json, project_id,
                        session_id, run_id, job_id, dedupe_key, expires_at,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT * FROM approval_requests
                    WHERE dedupe_key = ? AND status = ?
                    """,
                    (dedupe_key, ApprovalStatus.PENDING.value),
                ).fetchone()
                if row is None:
                    raise
                return _approval_request_from_row(row)
            if context.job_id:
                connection.execute(
                    """
                    UPDATE jobs SET approval_request_id = ?, updated_at = ?,
                        version = version + 1 WHERE id = ?
                    """,
                    (request_id, now, context.job_id),
                )
            if context.enforcement_owner:
                _append_policy_audit_event(
                    connection,
                    operation_id=request_id,
                    action=resolution.action,
                    phase=PolicyAuditPhase.RESOLUTION,
                    decision=resolution.decision.value,
                    enforcement=resolution.enforcement,
                    enforcement_owner=context.enforcement_owner,
                    policy_source=resolution.policy_source,
                    approval_request_id=request_id,
                    approval_binding_sha256=_approval_preview_binding(preview),
                    project_id=context.project_id,
                    session_id=context.session_id,
                    run_id=context.run_id,
                    job_id=context.job_id,
                    evidence={
                        "preview_sha256": _mapping_hash(preview, "policy preview")
                    },
                    created_at=now,
                )
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
        return _approval_request_from_row(row)

    def get_approval_request(self, request_id: str) -> ApprovalRequest:
        """Return one approval request after applying expiry."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            _expire_approvals(connection, now)
            row = connection.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return _approval_request_from_row(row)

    def find_approval_request_by_binding(
        self,
        approval_binding: str,
    ) -> ApprovalRequest | None:
        """Return the newest request bound to one exact external operation."""
        binding_sha256 = approval_binding_digest(approval_binding)
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            _expire_approvals(connection, now)
            rows = connection.execute(
                """
                SELECT * FROM approval_requests
                WHERE preview_json LIKE ?
                ORDER BY created_at DESC, id DESC
                LIMIT 20
                """,
                (f"%{binding_sha256}%",),
            ).fetchall()
        for row in rows:
            request = _approval_request_from_row(row)
            if _approval_preview_binding(request.preview) == binding_sha256:
                return request
        return None

    def list_approval_requests(
        self,
        *,
        status: ApprovalStatus | str | None = None,
        limit: int = 100,
    ) -> tuple[ApprovalRequest, ...]:
        """List newest approval inbox items with bounded output."""
        parsed_status = ApprovalStatus(status) if status is not None else None
        page_size = max(1, min(int(limit), 200))
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            _expire_approvals(connection, now)
            if parsed_status is None:
                rows = connection.execute(
                    "SELECT * FROM approval_requests ORDER BY created_at DESC, id DESC LIMIT ?",
                    (page_size,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM approval_requests WHERE status = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """,
                    (parsed_status.value, page_size),
                ).fetchall()
        return tuple(_approval_request_from_row(row) for row in rows)

    def list_run_approval_requests(
        self,
        *,
        run_id: str,
        job_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ApprovalRequest, ...]:
        """List approval records linked to one run or its durable job."""
        page_size = max(1, min(int(limit), 200))
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            _expire_approvals(connection, now)
            if job_id:
                rows = connection.execute(
                    """
                    SELECT * FROM approval_requests
                    WHERE run_id = ? OR job_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """,
                    (run_id, job_id, page_size),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM approval_requests WHERE run_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """,
                    (run_id, page_size),
                ).fetchall()
        return tuple(_approval_request_from_row(row) for row in rows)

    def attention_read_ids(self, item_ids: tuple[str, ...]) -> frozenset[str]:
        """Return the subset of derived attention item ids marked as read."""
        if not item_ids:
            return frozenset()
        safe_ids = tuple(_required_text(item_id, "item_id") for item_id in item_ids)
        placeholders = ", ".join("?" for _ in safe_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT item_id FROM attention_reads WHERE item_id IN ({placeholders})",
                safe_ids,
            ).fetchall()
        return frozenset(str(row[0]) for row in rows)

    def mark_attention_read(
        self, item_ids: tuple[str, ...], *, read: bool = True
    ) -> None:
        """Persist or clear acknowledgement without mutating source audit rows."""
        safe_ids = tuple(_required_text(item_id, "item_id") for item_id in item_ids)
        if not safe_ids:
            return
        with self._connect() as connection, _transaction(connection):
            if read:
                now = _utc_now()
                connection.executemany(
                    "INSERT OR REPLACE INTO attention_reads (item_id, read_at) VALUES (?, ?)",
                    ((item_id, now) for item_id in safe_ids),
                )
            else:
                placeholders = ", ".join("?" for _ in safe_ids)
                connection.execute(
                    f"DELETE FROM attention_reads WHERE item_id IN ({placeholders})",
                    safe_ids,
                )
