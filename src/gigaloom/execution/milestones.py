"""Persistence milestones for one run execution lifecycle."""

from enum import Enum


class PersistenceMilestone(str, Enum):
    """Name a durable boundary without prescribing its storage implementation."""

    ADMITTED = "admitted"
    RUN_STARTED = "run_started"
    REQUEST_STORED = "request_stored"
    RESPONSE_STORED = "response_stored"
    RUN_TERMINAL = "run_terminal"
    PROVENANCE_STORED = "provenance_stored"
