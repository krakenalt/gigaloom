"""Pane-aware liveness contracts for managed tmux terminals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gigaloom.native.terminal.contracts import TerminalState


class TerminalLivenessKind(str, Enum):
    """Observed tmux terminal liveness."""

    LIVE = "live"
    EXITED = "exited"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TerminalLiveness:
    """Content-free result of one pane-aware liveness probe."""

    kind: TerminalLivenessKind
    pane_pid: int | None = None
    exit_status: int | None = None

    @property
    def terminal_state(self) -> TerminalState:
        """Project liveness to a truthful registry lifecycle state."""
        if self.kind is TerminalLivenessKind.LIVE:
            return TerminalState.RUNNING
        if self.kind is TerminalLivenessKind.EXITED:
            return TerminalState.EXITED
        return TerminalState.ORPHANED
