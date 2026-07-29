"""Codex app-server constants and client contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Mapping, Protocol


APP_SERVER_PROTOCOL = "codex-app-server-json-rpc-v2"
APP_SERVER_LINK_SCHEMA_VERSION = 1
APP_SERVER_TIMEOUT_SECONDS = 10.0
APP_SERVER_MESSAGE_POLL_SECONDS = 0.1
APP_SERVER_ROLLOUT_POLL_SECONDS = 0.25
APP_SERVER_STDERR_CHARS = 8000
APP_SERVER_DRIVER_PROTOCOL_VERSION = "2"
APP_SERVER_APPROVAL_OWNER = "codex_app_server.approval"
APP_SERVER_APPROVAL_POLL_SECONDS = 0.05
_APPROVAL_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
    }
)


class AppServerProtocolError(RuntimeError):
    """Raised when app-server violates the reviewed JSON-RPC contract."""


class AppServerClient(Protocol):
    """Minimal synchronous JSON-RPC client used by the supervisor."""

    runtime_id: str

    @property
    def alive(self) -> bool:
        """Return whether the owning app-server process is still usable."""

    def request(
        self, method: str, params: Mapping[str, Any], *, timeout: float
    ) -> Mapping[str, Any]:
        """Send one request and return its result object."""

    def next_message(self, *, timeout: float) -> Mapping[str, Any] | None:
        """Return the next server notification or server-initiated request."""

    def respond(
        self,
        request_id: str | int,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        """Answer a server-initiated request."""

    def close(self) -> None:
        """Stop the owned transport."""


@dataclass
class _Runtime:
    scope_id: str
    client: AppServerClient
    loaded_threads: set[str] = field(default_factory=set)
    turn_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class _PendingApproval:
    request_id: str | int
    method: str
    params: Mapping[str, Any]
    durable_approval_id: str | None = None
