"""Public contracts and registry for managed native terminals."""

from gigaloom.native.terminal.contracts import (
    TerminalAccess,
    TerminalIdentity,
    TerminalRecord,
    TerminalState,
    terminal_record_to_dict,
)
from gigaloom.native.terminal.instance import (
    TmuxInstanceError,
    TmuxLaunchSpec,
    TmuxTerminalKernel,
    digest_terminal_command,
    digest_terminal_path,
)
from gigaloom.native.terminal.liveness import (
    TerminalLiveness,
    TerminalLivenessKind,
)
from gigaloom.native.terminal.registry import (
    ManagedTerminalRegistry,
    TerminalAccessDeniedError,
    TerminalCloseError,
    TerminalConflictError,
    TerminalLaunchError,
    TerminalNotFoundError,
)
from gigaloom.native.terminal.tmux import (
    TmuxCapability,
    TmuxCapabilityStatus,
    probe_tmux,
)

__all__ = [
    "ManagedTerminalRegistry",
    "TerminalAccess",
    "TerminalAccessDeniedError",
    "TerminalCloseError",
    "TerminalConflictError",
    "TerminalIdentity",
    "TerminalLaunchError",
    "TerminalLiveness",
    "TerminalLivenessKind",
    "TerminalNotFoundError",
    "TerminalRecord",
    "TerminalState",
    "TmuxCapability",
    "TmuxCapabilityStatus",
    "TmuxInstanceError",
    "TmuxLaunchSpec",
    "TmuxTerminalKernel",
    "digest_terminal_command",
    "digest_terminal_path",
    "probe_tmux",
    "terminal_record_to_dict",
]
