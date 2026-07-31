"""Inspect and export content-safe runtime coordination state."""

from __future__ import annotations

from typing import Any

from gigaloom.runtime.models import (
    attempt_to_dict,
    job_to_dict,
    native_process_record_to_dict,
    outbox_entry_to_dict,
    side_effect_to_dict,
    worker_to_dict,
)
from gigaloom.runtime.policy import (
    approval_grant_to_dict,
    approval_request_to_dict,
    policy_audit_event_to_dict,
)
from gigaloom.runtime.repositories.base import RuntimeRepository
from gigaloom.runtime.repositories.records import (
    _outbox_from_row,
    _safe_workflow_export_row,
    _utc_now,
)
from gigaloom.reviewed_evidence import reviewed_evidence_index
from gigaloom.sessions.contracts import redact_for_storage


class RuntimeDiagnosticsRepository(RuntimeRepository):
    """Inspect and export content-safe runtime coordination state."""

    def inspect(self) -> dict[str, Any]:
        """Return safe runtime metadata and row counts."""
        with self._connect() as connection:
            counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "jobs",
                    "job_attempts",
                    "runtime_outbox",
                    "trace_sequences",
                    "workers",
                    "approval_requests",
                    "approval_grants",
                    "policy_audit_events",
                    "workflow_runs",
                    "workflow_step_attempts",
                    "schedule_states",
                    "schedule_occurrences",
                    "attention_reads",
                    "native_processes",
                    "native_process_outputs",
                    "harness_side_effects",
                )
            }
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_outbox WHERE processed_at IS NULL"
                ).fetchone()[0]
            )
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        return {
            "path": str(self.path),
            "schema_version": self.schema_version,
            "journal_mode": journal_mode,
            "counts": counts,
            "pending_outbox": pending,
        }

    def export(self) -> dict[str, Any]:
        """Export coordination state as transparent, task-content-free JSON."""
        policy_audit_events = self.list_policy_audit_events(limit=1000)
        with self._connect() as connection:
            outbox_rows = connection.execute(
                "SELECT * FROM runtime_outbox ORDER BY created_at, id"
            ).fetchall()
            sequence_rows = connection.execute(
                "SELECT trace_id, last_sequence FROM trace_sequences ORDER BY trace_id"
            ).fetchall()
            workflow_rows = connection.execute(
                "SELECT * FROM workflow_runs ORDER BY created_at, id"
            ).fetchall()
            workflow_step_rows = connection.execute(
                "SELECT * FROM workflow_step_attempts ORDER BY workflow_run_id, created_at, id"
            ).fetchall()
            schedule_rows = connection.execute(
                "SELECT * FROM schedule_states ORDER BY project_id, schedule_id"
            ).fetchall()
            occurrence_rows = connection.execute(
                "SELECT * FROM schedule_occurrences ORDER BY created_at, id"
            ).fetchall()
            attention_rows = connection.execute(
                "SELECT * FROM attention_reads ORDER BY read_at, item_id"
            ).fetchall()
            native_output_rows = connection.execute(
                """
                SELECT process_id, cursor, stream, text, created_at
                FROM native_process_outputs ORDER BY process_id, cursor
                """
            ).fetchall()
        return {
            "schema_version": self.schema_version,
            "exported_at": _utc_now(),
            "jobs": [job_to_dict(job) for job in self.list_jobs()],
            "attempts": [attempt_to_dict(item) for item in self.list_attempts()],
            "side_effects": [
                side_effect_to_dict(item) for item in self.list_side_effects()
            ],
            "workers": [worker_to_dict(item) for item in self.list_workers()],
            "native_processes": [
                native_process_record_to_dict(item)
                for item in self.list_native_processes()
            ],
            "native_process_outputs": [
                {
                    "process_id": str(row["process_id"]),
                    "cursor": int(row["cursor"]),
                    "stream": str(row["stream"]),
                    "text": str(redact_for_storage(row["text"])),
                    "created_at": str(row["created_at"]),
                }
                for row in native_output_rows
            ],
            "approvals": [
                approval_request_to_dict(item)
                for item in self.list_approval_requests(limit=200)
            ],
            "approval_grants": [
                approval_grant_to_dict(item) for item in self.list_approval_grants()
            ],
            "policy_audit_events": [
                policy_audit_event_to_dict(item) for item in policy_audit_events
            ],
            "reviewed_evidence": reviewed_evidence_index(policy_audit_events),
            "attention_reads": [dict(row) for row in attention_rows],
            "outbox": [
                outbox_entry_to_dict(_outbox_from_row(row)) for row in outbox_rows
            ],
            "trace_sequences": [
                {"trace_id": str(row[0]), "last_sequence": int(row[1])}
                for row in sequence_rows
            ],
            "workflow_runs": [_safe_workflow_export_row(row) for row in workflow_rows],
            "workflow_step_attempts": [
                _safe_workflow_export_row(row) for row in workflow_step_rows
            ],
            "schedules": [_safe_workflow_export_row(row) for row in schedule_rows],
            "schedule_occurrences": [
                _safe_workflow_export_row(row) for row in occurrence_rows
            ],
        }
