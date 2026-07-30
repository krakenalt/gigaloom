"""Direct local tmux attach with truthful detach and exit projection."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from gigaloom.native.terminal.contracts import (
    TerminalAccess,
    TerminalRecord,
    TerminalState,
)
from gigaloom.native.terminal.lifecycle import (
    TerminalLifecycleAction,
    TerminalLifecycleOutcome,
    TerminalLifecycleReceipt,
)
from gigaloom.native.terminal.liveness import (
    TerminalLiveness,
    TerminalLivenessKind,
)
from gigaloom.native.terminal.recovery import TerminalMetadataStore
from gigaloom.native.terminal.registry import ManagedTerminalRegistry


class LocalAttachBackend(Protocol):
    """Structural backend contract implemented by the private tmux kernel."""

    def attach_local(self, terminal_id: str) -> int:
        """Attach inherited stdio and return the tmux client exit code."""

    def liveness(self, terminal_id: str) -> TerminalLiveness:
        """Return pane-aware liveness."""


class LocalTerminalAttachService:
    """Attach one authorized local terminal and persist its truthful result."""

    def __init__(
        self,
        registry: ManagedTerminalRegistry,
        store: TerminalMetadataStore,
        backend: LocalAttachBackend,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.backend = backend
        self._now = now or (lambda: datetime.now(timezone.utc))

    def attach(
        self,
        terminal_id: str,
        access: TerminalAccess,
        *,
        expected_revision: int,
    ) -> tuple[TerminalRecord, TerminalLifecycleReceipt]:
        """Directly attach and distinguish terminal detach from pane exit."""
        current = self.registry.get(
            terminal_id,
            access,
            expected_revision=expected_revision,
        )
        attached = self.registry.transition(
            terminal_id,
            access,
            TerminalState.ATTACHED,
            expected_revision=current.revision,
        )
        self.store.save(attached)
        liveness_observed = False
        try:
            returncode = self.backend.attach_local(terminal_id)
            observed = self.backend.liveness(terminal_id)
            liveness_observed = True
            if observed.kind is TerminalLivenessKind.LIVE:
                target = TerminalState.DETACHED
                outcome = TerminalLifecycleOutcome.DETACHED
                reason = "local_tmux_client_detached"
            elif observed.kind is TerminalLivenessKind.EXITED:
                target = TerminalState.EXITED
                outcome = TerminalLifecycleOutcome.EXITED
                reason = "terminal_pane_exited"
            else:
                target = TerminalState.ORPHANED
                outcome = TerminalLifecycleOutcome.ORPHANED
                reason = "terminal_backend_is_missing_or_unknown"
            if returncode != 0 and target is TerminalState.DETACHED:
                target = TerminalState.FAILED
                outcome = TerminalLifecycleOutcome.FAILED
                reason = "local_tmux_attach_failed"
        except Exception:
            target = TerminalState.FAILED
            outcome = TerminalLifecycleOutcome.FAILED
            reason = "local_tmux_attach_failed"
        update = (
            self.registry.observe_liveness
            if liveness_observed
            else self.registry.transition
        )
        updated = update(
            terminal_id,
            access,
            target,
            expected_revision=attached.revision,
        )
        self.store.save(updated)
        return updated, self._receipt(
            updated,
            previous_state=current.state,
            outcome=outcome,
            reason=reason,
        )

    def _receipt(
        self,
        record: TerminalRecord,
        *,
        previous_state: TerminalState,
        outcome: TerminalLifecycleOutcome,
        reason: str,
    ) -> TerminalLifecycleReceipt:
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("terminal attach clock must be timezone-aware")
        return TerminalLifecycleReceipt(
            terminal_id=record.id,
            action=TerminalLifecycleAction.LOCAL_ATTACH,
            outcome=outcome,
            previous_state=previous_state,
            current_state=record.state,
            revision=record.revision,
            created_at=timestamp.astimezone(timezone.utc).isoformat(),
            reason=reason,
        )
