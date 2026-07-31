"""ACP-native session cancellation and local waiter semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from acp.schema import CancelNotification

from gigaloom.harnesses.acp.sessions import AcpSessionBindingV1, require_session

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient


def cancel_session(client: AcpClient, binding: AcpSessionBindingV1) -> None:
    """Send the stable ACP session cancellation notification."""
    require_session(client, binding)
    notification = CancelNotification(session_id=binding.acp_session_id)
    client.supervisor.notify(
        "session/cancel",
        notification.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
