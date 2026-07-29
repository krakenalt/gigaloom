"""Repository for the workflows subcontext."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from uuid import uuid4
from gpt2giga_harness.projects.api import HarnessProject
from gpt2giga_harness.automation.ports import WorkflowStatus
from gpt2giga_harness.automation.ports import RuntimeCoordinationStore
from gpt2giga_harness.automation.ports import utc_now
from .codec import (
    _json as _json,
    _step_attempt_from_row as _step_attempt_from_row,
    _step_to_dict as _step_to_dict,
    _workflow_run_from_row as _workflow_run_from_row,
    workflow_definition_to_dict as workflow_definition_to_dict,
)
from .constants import (
    TERMINAL_STEP_STATUSES as TERMINAL_STEP_STATUSES,
    WORKFLOW_COORDINATION_OUTPUT as WORKFLOW_COORDINATION_OUTPUT,
)
from .models import (
    StepAttempt as StepAttempt,
    WorkflowDefinition as WorkflowDefinition,
    WorkflowRun as WorkflowRun,
)


class WorkflowRepository:
    """SQLite-backed workflow coordination repository."""

    def __init__(self, runtime_store: RuntimeCoordinationStore) -> None:
        self.runtime_store = runtime_store

    def create_run(
        self,
        definition: WorkflowDefinition,
        project: HarnessProject,
        session_id: str,
        inputs: Mapping[str, Any],
        *,
        run_id: str | None = None,
        coordination: Mapping[str, Any] | None = None,
    ) -> WorkflowRun:
        """Persist a run and immutable initial step snapshots atomically."""
        run_id = run_id or f"workflow_{uuid4().hex}"
        now = utc_now()
        definition_payload = workflow_definition_to_dict(definition)
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO workflow_runs (
                        id, workflow_id, definition_hash, schema_version, status,
                        project_id, project_root, session_id, definition_json,
                        inputs_json, outputs_json, max_concurrency, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        definition.id,
                        definition.source_hash or "",
                        definition.schema_version,
                        WorkflowStatus.QUEUED.value,
                        project.id,
                        project.root,
                        session_id,
                        _json(definition_payload),
                        _json(inputs),
                        _json(
                            {WORKFLOW_COORDINATION_OUTPUT: dict(coordination)}
                            if coordination
                            else {}
                        ),
                        definition.budgets.max_concurrency,
                        now,
                        now,
                    ),
                )
                for step in definition.steps:
                    connection.execute(
                        """
                        INSERT INTO workflow_step_attempts (
                            id, workflow_run_id, step_id, attempt_number, kind,
                            status, snapshot_json, inputs_json, created_at, updated_at
                        ) VALUES (?, ?, ?, 1, ?, 'pending', ?, '{}', ?, ?)
                        """,
                        (
                            f"step_{uuid4().hex}",
                            run_id,
                            step.id,
                            step.kind.value,
                            _json(_step_to_dict(step)),
                            now,
                            now,
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> WorkflowRun:
        """Return one workflow run."""
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _workflow_run_from_row(row)

    def list_runs(
        self,
        *,
        workflow_id: str | None = None,
        project_id: str | None = None,
    ) -> tuple[WorkflowRun, ...]:
        """List newest workflow runs."""
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            if workflow_id and project_id:
                rows = connection.execute(
                    """
                    SELECT * FROM workflow_runs
                    WHERE workflow_id = ? AND project_id = ?
                    ORDER BY created_at DESC
                    """,
                    (workflow_id, project_id),
                ).fetchall()
            elif workflow_id:
                rows = connection.execute(
                    "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY created_at DESC",
                    (workflow_id,),
                ).fetchall()
            elif project_id:
                rows = connection.execute(
                    "SELECT * FROM workflow_runs WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM workflow_runs ORDER BY created_at DESC"
                ).fetchall()
        return tuple(_workflow_run_from_row(row) for row in rows)

    def list_steps(self, run_id: str) -> tuple[StepAttempt, ...]:
        """Return stable definition order step attempts."""
        run = self.get_run(run_id)
        order = {
            str(item["id"]): index
            for index, item in enumerate(run.definition.get("steps", ()))
            if isinstance(item, Mapping)
        }
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT * FROM workflow_step_attempts WHERE workflow_run_id = ?",
                (run_id,),
            ).fetchall()
        attempts = [_step_attempt_from_row(row) for row in rows]
        attempts.sort(
            key=lambda item: (order.get(item.step_id, len(order)), item.attempt_number)
        )
        return tuple(attempts)

    def update_step(
        self,
        attempt_id: str,
        *,
        status: str,
        job_id: str | None = None,
        inputs: Mapping[str, Any] | None = None,
        outputs: Mapping[str, Any] | None = None,
        artifact_refs: Sequence[Mapping[str, Any]] | None = None,
        error_summary: str | None = None,
    ) -> StepAttempt:
        """Update one step projection under an immediate transaction."""
        now = utc_now()
        terminal = now if status in TERMINAL_STEP_STATUSES else None
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM workflow_step_attempts WHERE id = ?", (attempt_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(attempt_id)
                current = _step_attempt_from_row(row)
                connection.execute(
                    """
                    UPDATE workflow_step_attempts SET status = ?, job_id = ?,
                        inputs_json = ?, outputs_json = ?, artifact_refs_json = ?,
                        error_summary = ?, updated_at = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        job_id if job_id is not None else current.job_id,
                        _json(inputs if inputs is not None else current.inputs),
                        _json(outputs if outputs is not None else current.outputs),
                        _json(
                            artifact_refs
                            if artifact_refs is not None
                            else current.artifact_refs
                        ),
                        error_summary,
                        now,
                        terminal,
                        attempt_id,
                    ),
                )
                updated = connection.execute(
                    "SELECT * FROM workflow_step_attempts WHERE id = ?", (attempt_id,)
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return _step_attempt_from_row(updated)

    def update_run(
        self,
        run_id: str,
        status: WorkflowStatus,
        *,
        outputs: Mapping[str, Any] | None = None,
        error_summary: str | None = None,
        request_cancel: bool = False,
    ) -> WorkflowRun:
        """Update workflow lifecycle state."""
        now = utc_now()
        finished = (
            now
            if status
            in {
                WorkflowStatus.SUCCEEDED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELED,
            }
            else None
        )
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute(
                """
                UPDATE workflow_runs SET status = ?, outputs_json = COALESCE(?, outputs_json),
                    error_summary = ?, cancel_requested_at = CASE WHEN ? THEN ? ELSE cancel_requested_at END,
                    updated_at = ?, finished_at = ? WHERE id = ?
                """,
                (
                    status.value,
                    _json(outputs) if outputs is not None else None,
                    error_summary,
                    1 if request_cancel else 0,
                    now,
                    now,
                    finished,
                    run_id,
                ),
            )
        return self.get_run(run_id)
