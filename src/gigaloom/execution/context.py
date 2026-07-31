"""Typed state shared across one run execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gigaloom.execution.milestones import PersistenceMilestone
from gigaloom.execution.options import RunOptions
from gigaloom.sessions.api import HarnessMessage


@dataclass(frozen=True)
class RunExecutionContext:
    """Immutable admission context consumed by runner phases."""

    session: Any
    options: RunOptions
    harness: Any
    logical_user_message_id: str
    previous_messages: tuple[HarnessMessage, ...]
    provider_account_binding: Mapping[str, Any] | None
    milestone: PersistenceMilestone = PersistenceMilestone.ADMITTED
