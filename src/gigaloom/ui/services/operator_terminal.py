"""Browser-safe application boundary for managed terminal attach."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, urlencode

from gigaloom.native.terminal import (
    ManagedTerminalRegistry,
    TerminalAccess,
    TerminalAttachRequest,
    TerminalControlBackend,
    TerminalControlBridge,
    TerminalControlSession,
    TerminalOriginPolicy,
    TerminalRecord,
    TerminalState,
    TerminalWebSocketCloseCode,
    terminal_record_to_dict,
)


TERMINAL_BROWSER_SCHEMA_VERSION = 1
TERMINAL_BROWSER_KIND = "gigaloom.managed_terminal_browser_attach.v1"
_ATTACHABLE_STATES = frozenset(
    {
        TerminalState.RUNNING,
        TerminalState.ATTACHED,
        TerminalState.DETACHED,
    }
)


@dataclass(frozen=True, slots=True)
class TerminalBrowserProjection:
    """Content-free same-origin attach description for one terminal revision."""

    record: TerminalRecord
    websocket_path: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.record, TerminalRecord):
            raise ValueError("terminal browser record is invalid")
        expected = terminal_websocket_path(self.record)
        if self.record.state in _ATTACHABLE_STATES:
            if self.websocket_path != expected:
                raise ValueError("terminal browser attach path is invalid")
        elif self.websocket_path is not None:
            raise ValueError("non-attachable terminal cannot expose a WebSocket path")

    def to_dict(self) -> dict[str, object]:
        """Return the frozen browser contract without a private backend target."""
        return {
            "schema_version": TERMINAL_BROWSER_SCHEMA_VERSION,
            "kind": TERMINAL_BROWSER_KIND,
            "terminal": terminal_record_to_dict(self.record),
            "attachable": self.websocket_path is not None,
            "websocket_path": self.websocket_path,
            "transport": {
                "output": "binary",
                "input": "binary",
                "resize": {
                    "type": "resize",
                    "revision": self.record.revision,
                    "rows": {"minimum": 2, "maximum": 200},
                    "columns": {"minimum": 20, "maximum": 500},
                },
            },
            "reconnect": {
                "strategy": "reauthorize_and_resnapshot",
                "seed": "bounded_capture",
            },
            "escape_policy": {
                "clipboard_write": "blocked",
                "hyperlinks": "disabled",
                "window_operations": "disabled",
                "title_is_trusted_html": False,
            },
            "close_reasons": {
                str(int(code)): reason
                for code, reason in (
                    (
                        TerminalWebSocketCloseCode.PROTOCOL_ERROR,
                        "protocol_error",
                    ),
                    (TerminalWebSocketCloseCode.FORBIDDEN, "forbidden"),
                    (TerminalWebSocketCloseCode.NOT_FOUND, "not_found"),
                    (TerminalWebSocketCloseCode.DETACHED, "detached"),
                    (TerminalWebSocketCloseCode.EXITED, "exited"),
                    (TerminalWebSocketCloseCode.BACKPRESSURE, "backpressure"),
                    (TerminalWebSocketCloseCode.INTERNAL_ERROR, "internal_error"),
                )
            },
        }


class TerminalBrowserOwner(Protocol):
    """Owner-backed terminal description and authorized control-session factory."""

    def describe_terminal(
        self,
        *,
        terminal_id: str,
        owner_id: str,
        workspace_id: str,
        session_id: str,
        revision: int,
    ) -> TerminalBrowserProjection: ...

    def open_terminal(
        self,
        request: TerminalAttachRequest,
    ) -> TerminalControlSession: ...


class ManagedTerminalBrowserOwner:
    """Compose the public registry and bridge without exposing tmux identities."""

    def __init__(
        self,
        registry: ManagedTerminalRegistry,
        backend: TerminalControlBackend,
        *,
        allowed_origins: tuple[str, ...],
    ) -> None:
        self.registry = registry
        self.bridge = TerminalControlBridge(
            registry,
            backend,
            TerminalOriginPolicy(allowed_origins),
        )

    def describe_terminal(
        self,
        *,
        terminal_id: str,
        owner_id: str,
        workspace_id: str,
        session_id: str,
        revision: int,
    ) -> TerminalBrowserProjection:
        """Resolve one exact public record before creating an attach URL."""
        record = self.registry.get(
            terminal_id,
            TerminalAccess(
                owner_id=owner_id,
                workspace_id=workspace_id,
                session_id=session_id,
            ),
            expected_revision=revision,
        )
        return TerminalBrowserProjection(
            record=record,
            websocket_path=(
                terminal_websocket_path(record)
                if record.state in _ATTACHABLE_STATES
                else None
            ),
        )

    def open_terminal(
        self,
        request: TerminalAttachRequest,
    ) -> TerminalControlSession:
        """Open one authorized session and return its bounded reconnect seed."""
        return self.bridge.open(request)


def terminal_websocket_path(record: TerminalRecord) -> str:
    """Build the opaque same-origin route for one exact terminal revision."""
    query = urlencode(
        {
            "workspace_id": record.identity.workspace_id,
            "session_id": record.identity.session_id,
            "revision": record.revision,
        }
    )
    terminal_id = quote(record.id, safe="")
    return f"/api/operator/terminals/{terminal_id}/attach/ws?{query}"


__all__ = [
    "ManagedTerminalBrowserOwner",
    "TERMINAL_BROWSER_KIND",
    "TERMINAL_BROWSER_SCHEMA_VERSION",
    "TerminalBrowserOwner",
    "TerminalBrowserProjection",
    "terminal_websocket_path",
]
