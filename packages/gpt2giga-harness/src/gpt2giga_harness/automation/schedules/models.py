"""Models for scheduled automation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ScheduleDefinition:
    """One shareable, immutable-at-run-time schedule definition."""

    id: str
    title: str
    target_kind: str
    target_id: str
    target_hash: str
    target_snapshot: Mapping[str, Any]
    cadence_kind: str
    timezone: str
    start_at: str
    interval_seconds: float | None = None
    rrule_text: str | None = None
    prompt: str | None = None
    inputs: Mapping[str, Any] | None = None
    destination: str = "new_task"
    session_id: str | None = None
    workspace_policy: str = "worktree"
    timeout_seconds: float = 3600.0
    max_attempts: int = 1
    overlap_policy: str = "skip"
    max_concurrency: int = 1
    misfire_policy: str = "skip"
    misfire_grace_seconds: float = 60.0
    notifications: Mapping[str, Any] | None = None
    source_hash: str = ""


@dataclass(frozen=True)
class ScheduleOccurrence:
    """One persisted scheduled or manually triggered occurrence."""

    id: str
    schedule_id: str
    definition_hash: str
    scheduled_for: str
    trigger: str
    status: str
    destination_session_id: str | None = None
    history_cutoff: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    error_summary: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None


class ScheduleError(ValueError):
    """Raised for safe schedule validation or lifecycle failures."""


class ScheduleConflictError(ScheduleError):
    """Raised when a schedule changes after an optimistic preview."""
