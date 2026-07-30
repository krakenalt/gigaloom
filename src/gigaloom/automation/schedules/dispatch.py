"""Dispatch operations for scheduled automation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import yaml
from gigaloom.automation.evaluations.api import (
    eval_run_to_dict,
    eval_spec_from_mapping,
    queue_eval,
)
from gigaloom.projects.api import HarnessProject, resolve_project
from gigaloom.automation.ports import run_to_dict
from gigaloom.automation.ports import title_from_prompt
from gigaloom.automation.workflows.api import (
    WorkflowCoordinator,
    parse_workflow_definition,
    workflow_run_to_dict,
)
from .constants import (
    ACTIVE_OCCURRENCE_STATUSES as ACTIVE_OCCURRENCE_STATUSES,
    SCHEDULE_DIRECTORY as SCHEDULE_DIRECTORY,
)
from .definitions import (
    _hash as _hash,
    _manual_occurrence_id as _manual_occurrence_id,
    _occurrence_from_row as _occurrence_from_row,
    _optional_text as _optional_text,
    _utc_now as _utc_now,
    load_schedule as load_schedule,
    occurrence_to_dict as occurrence_to_dict,
)
from .models import (
    ScheduleDefinition as ScheduleDefinition,
    ScheduleError as ScheduleError,
    ScheduleOccurrence as ScheduleOccurrence,
)


class ScheduleDispatchMixin:
    """Cohesive schedule service behavior."""

    def _recover_dispatching_occurrences(self) -> int:
        """Resume schedule dispatch after owner loss without duplicating child state."""
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            rows = connection.execute(
                """
                SELECT schedule_occurrences.*, schedule_states.project_id,
                       schedule_states.project_root
                FROM schedule_occurrences
                JOIN schedule_states USING (schedule_key)
                WHERE schedule_occurrences.status = 'dispatching'
                ORDER BY schedule_occurrences.created_at, schedule_occurrences.id
                """
            ).fetchall()
        recovered = 0
        for row in rows:
            occurrence = _occurrence_from_row(row)
            try:
                project = resolve_project(
                    str(row["project_root"]), data_dir=self.eval_store.data_dir
                )
                if project.id != str(row["project_id"]):
                    raise ScheduleError(
                        "scheduled project identity changed; refusing recovery"
                    )
                definition = load_schedule(project.root, occurrence.schedule_id)
                if definition.source_hash != occurrence.definition_hash:
                    raise ScheduleError(
                        "schedule definition changed during dispatch recovery"
                    )
                if definition.target_kind not in {"agent", "preset", "workflow"}:
                    raise ScheduleError(
                        "scheduled-start recovery is not proven for this target kind"
                    )
                result, job_id, run_id, session_id = self._dispatch_target(
                    project,
                    definition,
                    occurrence,
                    dry_run=occurrence.trigger == "test",
                )
                del result
                self._finish_occurrence(
                    occurrence.id,
                    "queued",
                    job_id=job_id,
                    run_id=run_id,
                    session_id=session_id,
                )
                recovered += 1
            except Exception as exc:
                self._finish_occurrence(occurrence.id, "failed", error=str(exc))
        return recovered

    def worker_health(self) -> dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=15)
        online = [
            item
            for item in self.runtime_store.list_workers()
            if item.status == "online"
            and datetime.fromisoformat(item.heartbeat_at) >= cutoff
        ]
        return {"online": bool(online), "count": len(online)}

    def _create_occurrence(
        self,
        definition: ScheduleDefinition,
        *,
        schedule_key: str,
        trigger: str,
        scheduled_for: str,
        status: str = "claimed",
        error: str | None = None,
        idempotency_key: str | None = None,
    ) -> ScheduleOccurrence:
        occurrence_id = (
            _manual_occurrence_id(
                schedule_key,
                trigger,
                idempotency_key,
            )
            if idempotency_key is not None
            else f"occurrence_{uuid4().hex}"
        )
        now = _utc_now()
        session_id = (
            definition.session_id if definition.destination == "resume" else None
        )
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM schedule_occurrences WHERE id = ?",
                (occurrence_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["definition_hash"]) != definition.source_hash
                    or str(existing["trigger"]) != trigger
                ):
                    connection.rollback()
                    raise ScheduleError(
                        "idempotency key is already bound to a different schedule action"
                    )
                connection.commit()
                return _occurrence_from_row(existing)
            existing = connection.execute(
                """
                SELECT * FROM schedule_occurrences
                WHERE schedule_key = ? AND scheduled_for = ? AND trigger = ?
                """,
                (schedule_key, scheduled_for, trigger),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _occurrence_from_row(existing)
            active = connection.execute(
                "SELECT COUNT(*) FROM schedule_occurrences WHERE schedule_key = ? AND trigger != 'test' AND status IN ('claimed','dispatching','queued','running')",
                (schedule_key,),
            ).fetchone()[0]
            serialized = 0
            if session_id:
                serialized = connection.execute(
                    "SELECT COUNT(*) FROM schedule_occurrences WHERE destination_session_id = ? AND status IN ('claimed','dispatching','queued','running')",
                    (session_id,),
                ).fetchone()[0]
            if status == "claimed" and (
                (
                    active >= definition.max_concurrency
                    and definition.overlap_policy == "skip"
                )
                or serialized
            ):
                status, error = "skipped", "overlap policy skipped active occurrence"
            connection.execute(
                """
                INSERT INTO schedule_occurrences (
                    id, schedule_key, schedule_id, definition_hash, scheduled_for, trigger,
                    status, destination_session_id, history_cutoff, error_summary,
                    created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurrence_id,
                    schedule_key,
                    definition.id,
                    definition.source_hash,
                    scheduled_for,
                    trigger,
                    status,
                    session_id,
                    now if session_id else None,
                    error,
                    now,
                    now if status == "claimed" else None,
                    now if status not in ACTIVE_OCCURRENCE_STATUSES else None,
                ),
            )
            connection.execute(
                """
                UPDATE schedule_states
                SET last_status = ?, last_error = ?, updated_at = ?
                WHERE schedule_key = (
                    SELECT schedule_key FROM schedule_occurrences WHERE id = ?
                )
                """,
                (status, error, _utc_now(), occurrence_id),
            )
            connection.commit()
        return ScheduleOccurrence(
            occurrence_id,
            definition.id,
            definition.source_hash,
            scheduled_for,
            trigger,
            status,
            session_id,
            now if session_id else None,
            error_summary=error,
            created_at=now,
            started_at=now if status == "claimed" else None,
        )

    def _execute(
        self,
        project: HarnessProject,
        definition: ScheduleDefinition,
        occurrence: ScheduleOccurrence,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        occurrence, dispatch_claimed = self._claim_dispatch(occurrence.id)
        if not dispatch_claimed:
            return {"occurrence": occurrence_to_dict(occurrence), "result": None}
        try:
            result, job_id, run_id, session_id = self._dispatch_target(
                project, definition, occurrence, dry_run=dry_run
            )
        except Exception as exc:
            self._finish_occurrence(occurrence.id, "failed", error=str(exc))
            if occurrence.trigger == "test":
                raise ScheduleError(f"Scheduled target test failed: {exc}") from exc
            raise
        self._finish_occurrence(
            occurrence.id,
            "queued",
            job_id=job_id,
            run_id=run_id,
            session_id=session_id,
        )
        return {
            "occurrence": occurrence_to_dict(self._get_occurrence(occurrence.id)),
            "result": result,
        }

    def _claim_dispatch(self, occurrence_id: str) -> tuple[ScheduleOccurrence, bool]:
        """Atomically grant one delivery the right to create target state."""
        now = _utc_now()
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE schedule_occurrences
                SET status = 'dispatching', started_at = COALESCE(started_at, ?)
                WHERE id = ? AND status = 'claimed'
                """,
                (now, occurrence_id),
            )
            claimed = connection.execute("SELECT changes()").fetchone()[0] == 1
            row = connection.execute(
                "SELECT * FROM schedule_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(occurrence_id)
            if claimed:
                connection.execute(
                    """
                    UPDATE schedule_states
                    SET last_status = 'dispatching', last_error = NULL, updated_at = ?
                    WHERE schedule_key = ?
                    """,
                    (now, str(row["schedule_key"])),
                )
            connection.commit()
        return _occurrence_from_row(row), claimed

    def _sync_occurrences(self) -> None:
        """Project terminal job, workflow, and eval state into occurrence history."""
        from gigaloom.automation.ports import TERMINAL_JOB_STATUSES
        from gigaloom.automation.workflows.api import WorkflowRepository

        with self.runtime_store._connect() as connection:  # noqa: SLF001
            rows = connection.execute(
                """
                SELECT schedule_occurrences.*, schedule_states.project_root
                FROM schedule_occurrences
                JOIN schedule_states USING (schedule_key)
                WHERE schedule_occurrences.status IN ('queued', 'running')
                """
            ).fetchall()
        for row in rows:
            status = None
            error = None
            scheduled_eval_regression = False
            if row["job_id"]:
                job = self.runtime_store.get_job(str(row["job_id"]))
                if job.status in TERMINAL_JOB_STATUSES:
                    status = job.status.value
                    error = job.error_summary
                elif job.status.value == "running":
                    status = "running"
            elif row["run_id"]:
                try:
                    definition = load_schedule(
                        str(row["project_root"]), str(row["schedule_id"])
                    )
                    if definition.target_kind == "workflow":
                        current = WorkflowRepository(self.runtime_store).get_run(
                            str(row["run_id"])
                        )
                        status = current.status.value
                        error = current.error_summary
                    elif definition.target_kind == "eval":
                        current_eval = self.eval_store.get_any(str(row["run_id"]))
                        status = current_eval.status
                        if status == "failed" and row["trigger"] != "test":
                            summary = current_eval.summary
                            error = (
                                "scheduled eval regression: "
                                f"{summary.get('failed', 0)} failed, "
                                f"{summary.get('errors', 0)} errors across "
                                f"{summary.get('total', 0)} cells"
                            )
                            scheduled_eval_regression = True
                except (KeyError, ScheduleError):
                    continue
            if status in {"succeeded", "failed", "canceled", "passed"}:
                terminal_status = "succeeded" if status == "passed" else status
                self._finish_occurrence(
                    str(row["id"]),
                    terminal_status,
                    error=error,
                )
                if scheduled_eval_regression:
                    self._mark_attention(
                        str(row["schedule_key"]), error or "eval failed"
                    )
                if row["trigger"] == "test" and terminal_status == "succeeded":
                    now = _utc_now()
                    with self.runtime_store._connect() as connection:  # noqa: SLF001
                        connection.execute(
                            "UPDATE schedule_states SET tested_hash = ?, tested_at = ?, updated_at = ? WHERE schedule_key = ?",
                            (
                                str(row["definition_hash"]),
                                now,
                                now,
                                str(row["schedule_key"]),
                            ),
                        )
            elif status == "running":
                self._finish_occurrence(str(row["id"]), "running")

    def _dispatch_target(
        self,
        project: HarnessProject,
        definition: ScheduleDefinition,
        occurrence: ScheduleOccurrence,
        *,
        dry_run: bool,
    ) -> tuple[dict[str, Any], str | None, str | None, str | None]:
        snapshot = dict(definition.target_snapshot)
        if _hash(snapshot) != definition.target_hash:
            raise ScheduleError("target snapshot hash mismatch")
        if definition.target_kind in {"agent", "preset"}:
            prompt = definition.prompt or str(
                snapshot.get("prompt") or definition.title
            )
            if definition.target_kind == "agent" and snapshot.get("instructions"):
                prompt = (
                    f"Agent role instructions:\n{snapshot['instructions']}\n\n"
                    f"Task:\n{prompt}"
                )
            harness_id = str(
                snapshot.get("harness_id") or snapshot.get("harness") or "echo"
            )
            mode = str(snapshot.get("mode") or "read")
            session = (
                self.runner.store.get_session(definition.session_id)
                if definition.destination == "resume"
                else self.runner.create_session(
                    title=title_from_prompt(prompt),
                    workspace=project.root,
                    default_harness_id=harness_id,
                    default_model=_optional_text(snapshot.get("model")),
                    default_mode=mode,
                )
            )
            payload = {
                "harness_id": harness_id,
                "prompt": prompt,
                "model": snapshot.get("model"),
                "api_mode": snapshot.get("api_mode") or "v2",
                "invocation_mode": snapshot.get("invocation_mode") or "headless",
                "mode": mode,
                "workspace": project.root,
                "workspace_policy": "worktree",
                "timeout_seconds": definition.timeout_seconds,
                "max_attempts": definition.max_attempts,
                "permission_profile": "unattended",
                "schedule_id": definition.id,
                "agent_id": definition.target_id
                if definition.target_kind == "agent"
                else None,
                "dry_run": dry_run,
                "extra": {
                    "schedule_id": definition.id,
                    "schedule_hash": definition.source_hash,
                    "target_snapshot": snapshot,
                    "history_cutoff": occurrence.history_cutoff,
                },
            }
            if payload["invocation_mode"] == "native":
                payload["execution_transport"] = "native_structured"
            submission = self.dispatcher.submit(
                session.id,
                payload,
                idempotency_key=f"schedule:{definition.id}:{occurrence.id}",
                origin="scheduled",
            )
            return (
                run_to_dict(submission.queued.run),
                submission.job.id,
                submission.queued.run.id,
                session.id,
            )
        if definition.target_kind == "workflow":
            workflow = parse_workflow_definition(
                yaml.safe_dump(snapshot, sort_keys=False), allow_unknown=True
            )
            coordinator = WorkflowCoordinator(
                project=project,
                runtime_store=self.runtime_store,
                runner=self.runner,
                dispatcher=self.dispatcher,
                origin="scheduled",
                schedule_id=definition.id,
            )
            run = coordinator.start(
                workflow,
                inputs=dict(definition.inputs or {}),
                prompt=definition.prompt,
                idempotency_key=f"schedule:{definition.id}:{occurrence.id}",
            )
            return (
                workflow_run_to_dict(run, coordinator.repository.list_steps(run.id)),
                None,
                run.id,
                run.session_id,
            )
        if definition.target_kind == "eval":
            spec = eval_spec_from_mapping(
                snapshot,
                path=Path(project.root) / SCHEDULE_DIRECTORY / f"{definition.id}.yaml",
            )
            eval_run = queue_eval(
                runner=self.runner,
                dispatcher=self.dispatcher,
                eval_store=self.eval_store,
                project=project,
                spec=spec,
                dry_run=dry_run,
                origin="scheduled",
                schedule_id=definition.id,
            )
            return eval_run_to_dict(eval_run), None, eval_run.id, eval_run.session_id
        raise ScheduleError(f"Unsupported target kind: {definition.target_kind}")

    def _finish_occurrence(
        self,
        occurrence_id: str,
        status: str,
        *,
        job_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE schedule_occurrences SET status = ?, job_id = COALESCE(?, job_id), run_id = COALESCE(?, run_id), destination_session_id = COALESCE(?, destination_session_id), error_summary = ?, finished_at = CASE WHEN ? IN ('tested','succeeded','failed','canceled','skipped','misfired') THEN ? ELSE finished_at END WHERE id = ?",
                (
                    status,
                    job_id,
                    run_id,
                    session_id,
                    error,
                    status,
                    _utc_now(),
                    occurrence_id,
                ),
            )
            connection.execute(
                """
                UPDATE schedule_states
                SET last_status = ?, last_error = ?, updated_at = ?
                WHERE schedule_key = (
                    SELECT schedule_key FROM schedule_occurrences WHERE id = ?
                )
                """,
                (status, error, _utc_now(), occurrence_id),
            )

    def _get_occurrence(self, occurrence_id: str) -> ScheduleOccurrence:
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT * FROM schedule_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone()
        return _occurrence_from_row(row)

    def _mark_attention(self, schedule_key: str, error: str) -> None:
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE schedule_states SET status = 'needs_attention', enabled = 0, last_status = 'failed', last_error = ?, updated_at = ? WHERE schedule_key = ?",
                (error[:1000], _utc_now(), schedule_key),
            )
