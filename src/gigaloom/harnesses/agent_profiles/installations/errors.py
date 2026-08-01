"""Typed, content-free managed-agent installation failures."""

from __future__ import annotations

from gigaloom.contracts import AgentCleanupStatus, InstallationTransitionV1


class AgentInstallError(RuntimeError):
    """Terminal installation failure with bounded non-content evidence."""

    def __init__(
        self,
        reason_code: str,
        *,
        transitions: tuple[InstallationTransitionV1, ...] = (),
        cleanup_status: AgentCleanupStatus = AgentCleanupStatus.NOT_REQUIRED,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.transitions = transitions
        self.cleanup_status = cleanup_status


class AgentInstallCancelled(AgentInstallError):
    """Caller-requested cancellation observed at a transaction checkpoint."""
