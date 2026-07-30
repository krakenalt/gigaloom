"""Lifecycle operations for scheduled automation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping
from gigaloom.projects.api import HarnessProject, resolve_project
from gigaloom.automation.ports import exclusive_file_lock
from .constants import SCHEDULE_DIRECTORY as SCHEDULE_DIRECTORY
from .definitions import (
    _first_scheduled as _first_scheduled,
    _occurrence_from_row as _occurrence_from_row,
    _schedule_key as _schedule_key,
    _utc_now as _utc_now,
    build_schedule_definition as build_schedule_definition,
    discover_schedules as discover_schedules,
    load_schedule as load_schedule,
    next_occurrences as next_occurrences,
    occurrence_to_dict as occurrence_to_dict,
    save_schedule as save_schedule,
    schedule_definition_to_dict as schedule_definition_to_dict,
)
from .models import (
    ScheduleConflictError as ScheduleConflictError,
    ScheduleError as ScheduleError,
)


class ScheduleLifecycleMixin:
    """Cohesive schedule service behavior."""

    def upsert(
        self,
        project: HarnessProject,
        payload: Mapping[str, Any],
        *,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        definition = build_schedule_definition(project, payload)
        save_schedule(project, definition, expected_hash=expected_hash)
        next_run = _first_scheduled(definition)
        now = _utc_now()
        schedule_key = _schedule_key(project.id, definition.id)
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO schedule_states (
                    schedule_key, schedule_id, project_id, project_root, definition_hash,
                    definition_json, status, enabled, timezone, next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'paused', 0, ?, ?, ?, ?)
                ON CONFLICT(schedule_key) DO UPDATE SET
                    project_id=excluded.project_id, project_root=excluded.project_root,
                    definition_hash=excluded.definition_hash,
                    definition_json=excluded.definition_json, timezone=excluded.timezone,
                    next_run_at=excluded.next_run_at, status='paused', enabled=0,
                    tested_hash=NULL, tested_at=NULL, updated_at=excluded.updated_at
                """,
                (
                    schedule_key,
                    definition.id,
                    project.id,
                    project.root,
                    definition.source_hash,
                    json.dumps(
                        schedule_definition_to_dict(definition),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    definition.timezone,
                    next_run,
                    now,
                    now,
                ),
            )
            connection.commit()
        self.runtime_store.wake_workers()
        return self.detail(project, definition.id)

    def detail(self, project: HarnessProject, schedule_id: str) -> dict[str, Any]:
        definition = load_schedule(project.root, schedule_id)
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            state = connection.execute(
                "SELECT * FROM schedule_states WHERE project_id = ? AND schedule_id = ?",
                (project.id, schedule_id),
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM schedule_occurrences WHERE schedule_key = ? ORDER BY created_at DESC LIMIT 50",
                (_schedule_key(project.id, schedule_id),),
            ).fetchall()
        return {
            "definition": schedule_definition_to_dict(definition),
            "state": dict(state) if state is not None else None,
            "occurrences": [
                occurrence_to_dict(_occurrence_from_row(row)) for row in rows
            ],
            "preview": list(next_occurrences(definition)),
            "worker": self.worker_health(),
        }

    def list(self, project: HarnessProject) -> tuple[dict[str, Any], ...]:
        return tuple(
            self.detail(project, item.id) for item in discover_schedules(project.root)
        )

    def automation_overview(self, project: HarnessProject) -> dict[str, Any]:
        """Return live definitions plus archived audit rows for the UI center."""
        live = {item["definition"]["id"]: item for item in self.list(project)}
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            states = connection.execute(
                "SELECT * FROM schedule_states WHERE project_id = ? ORDER BY updated_at DESC, schedule_id",
                (project.id,),
            ).fetchall()
            occurrence_rows = connection.execute(
                """
                SELECT schedule_occurrences.* FROM schedule_occurrences
                JOIN schedule_states USING (schedule_key)
                WHERE schedule_states.project_id = ?
                ORDER BY schedule_occurrences.created_at DESC LIMIT 200
                """,
                (project.id,),
            ).fetchall()
        schedules: list[dict[str, Any]] = list(live.values())
        for state in states:
            schedule_id = str(state["schedule_id"])
            if schedule_id in live:
                continue
            try:
                definition = json.loads(str(state["definition_json"] or "{}"))
            except json.JSONDecodeError:
                definition = {}
            schedules.append(
                {
                    "definition": definition
                    or {
                        "id": schedule_id,
                        "title": schedule_id,
                        "source_hash": str(state["definition_hash"]),
                    },
                    "state": dict(state),
                    "occurrences": [],
                    "preview": [],
                    "worker": self.worker_health(),
                }
            )
        schedules.sort(
            key=lambda item: (
                str((item.get("state") or {}).get("status") == "archived"),
                str(item["definition"].get("title") or item["definition"].get("id")),
            )
        )
        return {
            "schedules": schedules,
            "history": [
                occurrence_to_dict(_occurrence_from_row(row)) for row in occurrence_rows
            ],
            "worker": self.worker_health(),
        }

    def test_now(
        self,
        project: HarnessProject,
        schedule_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        definition = load_schedule(project.root, schedule_id)
        occurrence = self._create_occurrence(
            definition,
            schedule_key=_schedule_key(project.id, schedule_id),
            trigger="test",
            scheduled_for=_utc_now(),
            idempotency_key=idempotency_key,
        )
        return self._execute(project, definition, occurrence, dry_run=True)

    def enable(self, project: HarnessProject, schedule_id: str) -> dict[str, Any]:
        definition = load_schedule(project.root, schedule_id)
        if not self.worker_health()["online"]:
            raise ScheduleError("A local durable worker must be online before enable")
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT tested_hash FROM schedule_states WHERE schedule_key = ?",
                (_schedule_key(project.id, schedule_id),),
            ).fetchone()
            if row is None or row[0] != definition.source_hash:
                raise ScheduleError(
                    "Test now must succeed for this exact schedule hash before enable"
                )
            connection.execute(
                "UPDATE schedule_states SET enabled = 1, status = 'active', next_run_at = ?, updated_at = ? WHERE schedule_key = ?",
                (
                    _first_scheduled(definition),
                    _utc_now(),
                    _schedule_key(project.id, schedule_id),
                ),
            )
        self.runtime_store.wake_workers()
        return self.detail(project, schedule_id)

    def pause(self, project: HarnessProject, schedule_id: str) -> dict[str, Any]:
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE schedule_states SET enabled = 0, status = 'paused', updated_at = ? WHERE schedule_key = ?",
                (_utc_now(), _schedule_key(project.id, schedule_id)),
            )
        self.runtime_store.wake_workers()
        return self.detail(project, schedule_id)

    def archive(
        self,
        project: HarnessProject,
        schedule_id: str,
        *,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        """Remove the shareable definition while retaining immutable audit rows."""
        path = Path(project.root) / SCHEDULE_DIRECTORY / f"{schedule_id}.yaml"
        with exclusive_file_lock(path):
            definition = load_schedule(project.root, schedule_id)
            if expected_hash is not None and definition.source_hash != expected_hash:
                raise ScheduleConflictError("Schedule changed since the delete preview")
            self.pause(project, schedule_id)
            path.unlink()
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE schedule_states SET status = 'archived', enabled = 0, definition_json = ?, updated_at = ? WHERE schedule_key = ?",
                (
                    json.dumps(
                        schedule_definition_to_dict(definition),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    _utc_now(),
                    _schedule_key(project.id, schedule_id),
                ),
            )
        return {"archived": True, "schedule_id": schedule_id}

    def run_now(
        self,
        project: HarnessProject,
        schedule_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        definition = load_schedule(project.root, schedule_id)
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            state = connection.execute(
                "SELECT tested_hash FROM schedule_states WHERE schedule_key = ?",
                (_schedule_key(project.id, schedule_id),),
            ).fetchone()
        if state is None or state[0] != definition.source_hash:
            raise ScheduleError(
                "Test now must succeed for this exact schedule hash before run-now"
            )
        occurrence = self._create_occurrence(
            definition,
            schedule_key=_schedule_key(project.id, schedule_id),
            trigger="run_now",
            scheduled_for=_utc_now(),
            idempotency_key=idempotency_key,
        )
        return self._execute(project, definition, occurrence, dry_run=False)

    def tick(self) -> int:
        """Dispatch all currently due definitions; called by the local worker."""
        self._sync_occurrences()
        recovered = self._recover_dispatching_occurrences()
        now = _utc_now()
        with self.runtime_store._connect() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT * FROM schedule_states WHERE enabled = 1 AND status = 'active' AND next_run_at <= ? ORDER BY next_run_at",
                (now,),
            ).fetchall()
        dispatched = recovered
        for state in rows:
            try:
                project = resolve_project(
                    str(state["project_root"]),
                    data_dir=self.eval_store.data_dir,
                )
                if project.id != str(state["project_id"]):
                    raise ScheduleError(
                        "scheduled project identity changed; refusing dispatch"
                    )
                definition = load_schedule(project.root, str(state["schedule_id"]))
                scheduled_for = str(state["next_run_at"])
                future = next_occurrences(
                    definition,
                    after=datetime.fromisoformat(scheduled_for),
                    count=8,
                )
                next_run = next(
                    (str(item["utc"]) for item in future if item["utc"]), None
                )
                for item in future:
                    if item["status"] != "misfire":
                        break
                    self._create_occurrence(
                        definition,
                        schedule_key=str(state["schedule_key"]),
                        trigger="schedule",
                        scheduled_for=(f"{item['local']}[{definition.timezone}]"),
                        status="misfired",
                        error=str(item["reason"]),
                    )
                delay = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(scheduled_for)
                ).total_seconds()
                if (
                    delay > definition.misfire_grace_seconds
                    and definition.misfire_policy == "skip"
                ):
                    self._create_occurrence(
                        definition,
                        schedule_key=str(state["schedule_key"]),
                        trigger="schedule",
                        scheduled_for=scheduled_for,
                        status="misfired",
                        error="missed occurrence; automatic catch-up disabled",
                    )
                else:
                    occurrence = self._create_occurrence(
                        definition,
                        schedule_key=str(state["schedule_key"]),
                        trigger="schedule",
                        scheduled_for=scheduled_for,
                    )
                    if occurrence.status == "claimed":
                        self._execute(project, definition, occurrence, dry_run=False)
                        dispatched += 1
                with self.runtime_store._connect() as connection:  # noqa: SLF001
                    connection.execute(
                        "UPDATE schedule_states SET next_run_at = ?, last_run_at = ?, updated_at = ? WHERE schedule_key = ?",
                        (
                            next_run,
                            scheduled_for,
                            _utc_now(),
                            str(state["schedule_key"]),
                        ),
                    )
            except Exception as exc:
                self._mark_attention(str(state["schedule_key"]), str(exc))
        return dispatched
