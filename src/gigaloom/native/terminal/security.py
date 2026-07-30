"""Origin and authority checks for managed terminal browser attach."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from gigaloom.native.terminal.contracts import TerminalAccess, TerminalRecord
from gigaloom.native.terminal.websocket_protocol import TerminalWebSocketCloseCode


class TerminalAttachRejectedError(PermissionError):
    """Reject a browser attach with one explicit WebSocket close code."""

    def __init__(
        self,
        code: TerminalWebSocketCloseCode,
        reason: str,
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class TerminalAttachRequest:
    """Opaque browser attach identity before backend target resolution."""

    terminal_id: str
    owner_id: str
    workspace_id: str
    session_id: str
    revision: int
    origin: str

    def __post_init__(self) -> None:
        if not isinstance(self.terminal_id, str) or not self.terminal_id.strip():
            raise ValueError("terminal id is invalid")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("terminal revision is invalid")
        self.access

    @property
    def access(self) -> TerminalAccess:
        """Return the exact registry access binding."""
        return TerminalAccess(
            owner_id=self.owner_id,
            workspace_id=self.workspace_id,
            session_id=self.session_id,
        )


class TerminalOriginPolicy:
    """Admit only exact configured HTTP(S) browser origins."""

    def __init__(self, allowed_origins: tuple[str, ...]) -> None:
        if not allowed_origins:
            raise ValueError("at least one terminal origin is required")
        normalized = tuple(_validate_origin(item) for item in allowed_origins)
        if len(set(normalized)) != len(normalized):
            raise ValueError("terminal origins must be unique")
        self._allowed_origins = frozenset(normalized)

    def authorize(self, origin: str) -> None:
        """Reject an absent, malformed, or non-exact browser origin."""
        try:
            normalized = _validate_origin(origin)
        except ValueError as exc:
            raise TerminalAttachRejectedError(
                TerminalWebSocketCloseCode.FORBIDDEN,
                "terminal_origin_forbidden",
            ) from exc
        if normalized not in self._allowed_origins:
            raise TerminalAttachRejectedError(
                TerminalWebSocketCloseCode.FORBIDDEN,
                "terminal_origin_forbidden",
            )


def authorize_terminal_record(
    request: TerminalAttachRequest,
    record: TerminalRecord,
) -> None:
    """Revalidate opaque id, authority binding, and optimistic revision."""
    if record.id != request.terminal_id or record.identity.access != request.access:
        raise TerminalAttachRejectedError(
            TerminalWebSocketCloseCode.FORBIDDEN,
            "terminal_binding_forbidden",
        )
    if record.revision != request.revision:
        raise TerminalAttachRejectedError(
            TerminalWebSocketCloseCode.FORBIDDEN,
            "terminal_revision_changed",
        )


def _validate_origin(origin: str) -> str:
    if not isinstance(origin, str) or not origin or origin != origin.strip():
        raise ValueError("terminal origin is invalid")
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("terminal origin is invalid")
    return origin
