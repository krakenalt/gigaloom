"""Public contracts and registry for managed native terminals."""

from gigaloom.native.terminal.contracts import (
    TerminalAccess,
    TerminalIdentity,
    TerminalRecord,
    TerminalState,
    terminal_record_to_dict,
)
from gigaloom.native.terminal.registry import (
    ManagedTerminalRegistry,
    TerminalAccessDeniedError,
    TerminalCloseError,
    TerminalConflictError,
    TerminalLaunchError,
    TerminalNotFoundError,
)

__all__ = [
    "ManagedTerminalRegistry",
    "TerminalAccess",
    "TerminalAccessDeniedError",
    "TerminalCloseError",
    "TerminalConflictError",
    "TerminalIdentity",
    "TerminalLaunchError",
    "TerminalNotFoundError",
    "TerminalRecord",
    "TerminalState",
    "terminal_record_to_dict",
]
