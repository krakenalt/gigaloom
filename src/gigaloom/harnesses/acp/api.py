"""Public facade for the generic ACP gateway."""

from gigaloom.harnesses.acp.client import AcpClient, create_acp_client
from gigaloom.harnesses.acp.contracts import (
    ACP_PROTOCOL_VERSION,
    AcpCapabilitySnapshotV1,
    AcpClientInfo,
    AcpLimits,
    AcpProcessSpec,
    AcpRouteIdentity,
)
from gigaloom.harnesses.acp.permissions import (
    AcpPermissionContextV1,
    AcpPermissionRequestV1,
    next_permission,
    respond_permission,
)
from gigaloom.harnesses.acp.process import pin_acp_process
from gigaloom.harnesses.acp.prompts import AcpPromptHandle, begin_prompt
from gigaloom.harnesses.acp.sessions import (
    AcpSessionBindingV1,
    close_session,
    delete_session,
    list_sessions,
    load_session,
    new_session,
    resume_session,
    set_session_config,
)

__all__ = [
    "ACP_PROTOCOL_VERSION",
    "AcpCapabilitySnapshotV1",
    "AcpClient",
    "AcpClientInfo",
    "AcpLimits",
    "AcpProcessSpec",
    "AcpPromptHandle",
    "AcpRouteIdentity",
    "AcpSessionBindingV1",
    "AcpPermissionContextV1",
    "AcpPermissionRequestV1",
    "begin_prompt",
    "close_session",
    "create_acp_client",
    "delete_session",
    "list_sessions",
    "load_session",
    "new_session",
    "next_permission",
    "pin_acp_process",
    "respond_permission",
    "resume_session",
    "set_session_config",
]
