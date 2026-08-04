"""Public facade for the generic ACP gateway."""

from gigaloom.harnesses.acp.authentication import (
    AcpAuthenticationReceiptV1,
    authenticate,
    logout,
)
from gigaloom.harnesses.acp.cancellation import cancel_session
from gigaloom.harnesses.acp.client import AcpClient, create_acp_client
from gigaloom.harnesses.acp.conformance import (
    AcpProbeReceiptV1,
    run_non_persisting_probe,
)
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
from gigaloom.harnesses.acp.providers import (
    AcpProviderV1,
    configure_provider,
    disable_provider,
    list_providers,
)
from gigaloom.harnesses.acp.prompts import (
    AcpPromptHandle,
    AcpPromptResultV1,
    begin_prompt,
)
from gigaloom.harnesses.acp.sessions import (
    AcpSessionBindingV1,
    AcpSessionPageV1,
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
    "AcpAuthenticationReceiptV1",
    "AcpCapabilitySnapshotV1",
    "AcpClient",
    "AcpClientInfo",
    "AcpLimits",
    "AcpProcessSpec",
    "AcpProviderV1",
    "AcpProbeReceiptV1",
    "AcpPromptHandle",
    "AcpPromptResultV1",
    "AcpRouteIdentity",
    "AcpSessionBindingV1",
    "AcpSessionPageV1",
    "AcpPermissionContextV1",
    "AcpPermissionRequestV1",
    "authenticate",
    "begin_prompt",
    "cancel_session",
    "close_session",
    "configure_provider",
    "create_acp_client",
    "delete_session",
    "disable_provider",
    "list_providers",
    "list_sessions",
    "logout",
    "load_session",
    "new_session",
    "next_permission",
    "pin_acp_process",
    "respond_permission",
    "resume_session",
    "run_non_persisting_probe",
    "set_session_config",
]
