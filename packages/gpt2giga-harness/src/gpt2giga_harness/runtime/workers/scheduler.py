"""Cadence state for durable worker maintenance."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import time
from typing import Final

HEARTBEAT: Final[str] = "heartbeat"
SCHEDULES: Final[str] = "schedules"
RETRIES: Final[str] = "retries"
RECOVERY: Final[str] = "recovery"
RECONCILIATION: Final[str] = "reconciliation"
MAINTENANCE_TASKS: Final[tuple[str, ...]] = (
    HEARTBEAT,
    SCHEDULES,
    RETRIES,
    RECOVERY,
    RECONCILIATION,
)


class WorkerMaintenanceScheduler:
    """Track independent monotonic deadlines for worker maintenance tasks."""

    def __init__(
        self,
        *,
        heartbeat_seconds: float,
        schedule_seconds: float = 5.0,
        retry_seconds: float = 5.0,
        recovery_seconds: float = 5.0,
        reconciliation_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._cadences = {
            HEARTBEAT: max(float(heartbeat_seconds), 0.1),
            SCHEDULES: max(float(schedule_seconds), 0.1),
            RETRIES: max(float(retry_seconds), 0.1),
            RECOVERY: max(float(recovery_seconds), 0.1),
            RECONCILIATION: max(float(reconciliation_seconds), 0.1),
        }
        now = self._clock()
        self._next_due = {task: now for task in MAINTENANCE_TASKS}

    @property
    def next_due_timestamps(self) -> Mapping[str, float]:
        """Return a snapshot of the next monotonic deadline for each task."""
        return dict(self._next_due)

    def is_due(self, task: str) -> bool:
        """Return whether one maintenance task should run now."""
        self._require_task(task)
        return self._clock() >= self._next_due[task]

    def complete(self, task: str) -> None:
        """Advance one successfully completed task to its next cadence."""
        self._require_task(task)
        self._next_due[task] = self._clock() + self._cadences[task]

    def request(self, *tasks: str, delay_seconds: float = 0.0) -> None:
        """Make selected tasks due no later than the requested delay."""
        deadline = self._clock() + max(float(delay_seconds), 0.0)
        for task in tasks:
            self._require_task(task)
            self._next_due[task] = min(self._next_due[task], deadline)

    def next_delay(self, maximum_seconds: float) -> float:
        """Bound a wait by the earliest maintenance deadline."""
        maximum = max(float(maximum_seconds), 0.0)
        now = self._clock()
        due_in = min(max(deadline - now, 0.0) for deadline in self._next_due.values())
        return min(maximum, due_in)

    @staticmethod
    def _require_task(task: str) -> None:
        if task not in MAINTENANCE_TASKS:
            raise ValueError(f"unknown worker maintenance task: {task}")
