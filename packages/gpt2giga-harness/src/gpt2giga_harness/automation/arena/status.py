"""Status for the arena subcontext."""

from __future__ import annotations

from .models import HarnessArenaChildRun as HarnessArenaChildRun


def _arena_status(
    children: tuple[HarnessArenaChildRun, ...],
    *,
    expected_count: int,
) -> str:
    if len(children) < expected_count:
        return "running"
    statuses = {child.status for child in children}
    if statuses & {"queued", "running", "retry_wait"}:
        return "running"
    if statuses == {"succeeded"}:
        return "succeeded"
    if "succeeded" in statuses:
        return "partial"
    if "canceled" in statuses:
        return "canceled"
    return "failed"
