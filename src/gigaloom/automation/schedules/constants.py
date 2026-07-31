"""Constants for scheduled automation."""

from __future__ import annotations

from pathlib import Path


SCHEDULE_DIRECTORY = Path(".giga") / "schedules"


ACTIVE_OCCURRENCE_STATUSES = ("claimed", "dispatching", "queued", "running")


DEFAULT_PREVIEW_COUNT = 5
