"""Content-free lifecycle outcomes and receipts for managed terminals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from gigaloom.native.terminal.contracts import TerminalState


class TerminalLifecycleAction(str, Enum):
    """Auditable terminal lifecycle action."""

    LOCAL_ATTACH = "local_attach"
    RECONCILE = "reconcile"
    REAP = "reap"


class TerminalLifecycleOutcome(str, Enum):
    """Content-free outcome of one lifecycle action."""

    ATTACHED = "attached"
    DETACHED = "detached"
    EXITED = "exited"
    FAILED = "failed"
    ORPHANED = "orphaned"
    CLOSED = "closed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class TerminalLifecycleReceipt:
    """Content-free evidence for one terminal lifecycle decision."""

    terminal_id: str
    action: TerminalLifecycleAction
    outcome: TerminalLifecycleOutcome
    previous_state: TerminalState
    current_state: TerminalState
    revision: int
    created_at: str
    reason: str


def terminal_lifecycle_receipt_to_dict(
    receipt: TerminalLifecycleReceipt,
) -> dict[str, Any]:
    """Serialize a receipt without terminal bytes or backend targets."""
    return {
        "terminal_id": receipt.terminal_id,
        "action": receipt.action.value,
        "outcome": receipt.outcome.value,
        "previous_state": receipt.previous_state.value,
        "current_state": receipt.current_state.value,
        "revision": receipt.revision,
        "created_at": receipt.created_at,
        "reason": receipt.reason,
    }
