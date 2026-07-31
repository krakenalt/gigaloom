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
    TerminalControlBackend,
    TerminalControlBridge,
    TerminalControlClient,
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
from gigaloom.native.terminal.lifecycle import (
    TerminalLifecycleAction,
    TerminalLifecycleOutcome,
    TerminalLifecycleReceipt,
    terminal_lifecycle_receipt_to_dict,
)
from gigaloom.native.terminal.local_attach import LocalTerminalAttachService
from gigaloom.native.terminal.recovery import (
    TerminalMetadataStore,
    TerminalRecoveryManager,
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
    "LocalTerminalAttachService",
    "TerminalAccess",
    "TerminalAccessDeniedError",
    "TerminalAttachRejectedError",
    "TerminalAttachRequest",
    "TerminalBackpressureError",
    "TerminalCloseError",
    "TerminalConflictError",
    "TerminalControlBackend",
    "TerminalControlBridge",
    "TerminalControlClient",
    "TerminalControlSession",
    "TerminalIdentity",
    "TerminalInputFrame",
    "TerminalLaunchError",
    "TerminalLifecycleAction",
    "TerminalLifecycleOutcome",
    "TerminalLifecycleReceipt",
    "TerminalLiveness",
    "TerminalLivenessKind",
    "TerminalNotFoundError",
    "TerminalMetadataStore",
    "TerminalOriginPolicy",
    "TerminalRecord",
    "TerminalRecoveryManager",
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
    "terminal_lifecycle_receipt_to_dict",
]
