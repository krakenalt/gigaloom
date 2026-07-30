"""Public contracts and registry for managed native terminals."""

from gigaloom.native.terminal.contracts import (
    TerminalAccess,
    TerminalIdentity,
    TerminalRecord,
    TerminalState,
    terminal_record_to_dict,
)
from gigaloom.native.terminal.control_bridge import (
    BoundedTerminalOutputQueue,
    TerminalBackpressureError,
    TerminalControlBridge,
    TerminalControlSession,
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
from gigaloom.native.terminal.security import (
    TerminalAttachRejectedError,
    TerminalAttachRequest,
    TerminalOriginPolicy,
)
from gigaloom.native.terminal.tmux import (
    TmuxCapability,
    TmuxCapabilityStatus,
    decode_tmux_escaped_bytes,
    probe_tmux,
)
from gigaloom.native.terminal.websocket_protocol import (
    TerminalInputFrame,
    TerminalResizeFrame,
    TerminalWebSocketCloseCode,
    parse_terminal_client_frame,
    terminal_resize_frame_json,
)

__all__ = [
    "BoundedTerminalOutputQueue",
    "ManagedTerminalRegistry",
    "TerminalAccess",
    "TerminalAccessDeniedError",
    "TerminalAttachRejectedError",
    "TerminalAttachRequest",
    "TerminalBackpressureError",
    "TerminalCloseError",
    "TerminalConflictError",
    "TerminalControlBridge",
    "TerminalControlSession",
    "TerminalIdentity",
    "TerminalInputFrame",
    "TerminalLaunchError",
    "TerminalLiveness",
    "TerminalLivenessKind",
    "TerminalNotFoundError",
    "TerminalOriginPolicy",
    "TerminalRecord",
    "TerminalResizeFrame",
    "TerminalState",
    "TerminalWebSocketCloseCode",
    "TmuxCapability",
    "TmuxCapabilityStatus",
    "TmuxInstanceError",
    "TmuxLaunchSpec",
    "TmuxTerminalKernel",
    "decode_tmux_escaped_bytes",
    "digest_terminal_command",
    "digest_terminal_path",
    "parse_terminal_client_frame",
    "probe_tmux",
    "terminal_resize_frame_json",
    "terminal_record_to_dict",
]
