"""Content-free contracts for provider-neutral managed terminals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Any


MAX_TERMINAL_IDENTITY_CHARS = 256
_SHA256_PATTERN = re.compile(r"(?:sha256:)?[0-9a-f]{64}")


class TerminalState(str, Enum):
    """Lifecycle state for one managed terminal."""

    STARTING = "starting"
    RUNNING = "running"
    ATTACHED = "attached"
    DETACHED = "detached"
    EXITED = "exited"
    FAILED = "failed"
    ORPHANED = "orphaned"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True)
class TerminalAccess:
    """Exact authority binding required to resolve a terminal."""

    owner_id: str
    workspace_id: str
    session_id: str

    def __post_init__(self) -> None:
        _validate_identity(self.owner_id, field_name="owner id")
        _validate_identity(self.workspace_id, field_name="workspace id")
        _validate_identity(self.session_id, field_name="session id")


@dataclass(frozen=True)
class TerminalIdentity(TerminalAccess):
    """Immutable content-free identity for one managed terminal instance."""

    terminal_name: str
    session_key: str
    native_harness_id: str
    command_digest: str
    cwd_digest: str
    executable_path_digest: str
    executable_version: str
    native_session_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_identity(self.terminal_name, field_name="terminal name")
        _validate_identity(self.session_key, field_name="session key")
        _validate_identity(self.native_harness_id, field_name="native harness id")
        _validate_digest(self.command_digest, field_name="command digest")
        _validate_digest(self.cwd_digest, field_name="cwd digest")
        _validate_digest(
            self.executable_path_digest,
            field_name="executable path digest",
        )
        _validate_identity(self.executable_version, field_name="executable version")
        if self.native_session_id is not None:
            _validate_identity(
                self.native_session_id,
                field_name="native session id",
            )

    @property
    def access(self) -> TerminalAccess:
        """Return the exact owner/workspace/session authority binding."""
        return TerminalAccess(
            owner_id=self.owner_id,
            workspace_id=self.workspace_id,
            session_id=self.session_id,
        )

    @property
    def registry_key(self) -> tuple[str, str, str, str, str]:
        """Return the stable ensure-lock key without exposing process targets."""
        return (
            self.owner_id,
            self.workspace_id,
            self.session_id,
            self.terminal_name,
            self.session_key,
        )


@dataclass(frozen=True)
class TerminalRecord:
    """Public metadata for one managed terminal without socket or raw bytes."""

    id: str
    identity: TerminalIdentity
    state: TerminalState
    revision: int
    created_at: str
    updated_at: str
    last_attached_at: str | None = None
    last_liveness_at: str | None = None

    def __post_init__(self) -> None:
        _validate_identity(self.id, field_name="terminal id")
        if not isinstance(self.identity, TerminalIdentity):
            raise ValueError("terminal identity is invalid")
        if not isinstance(self.state, TerminalState):
            raise ValueError("terminal state is invalid")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("terminal revision must be positive")
        _validate_timestamp(self.created_at, field_name="created at")
        _validate_timestamp(self.updated_at, field_name="updated at")
        if self.last_attached_at is not None:
            _validate_timestamp(
                self.last_attached_at,
                field_name="last attached at",
            )
        if self.last_liveness_at is not None:
            _validate_timestamp(
                self.last_liveness_at,
                field_name="last liveness at",
            )


def terminal_record_to_dict(record: TerminalRecord) -> dict[str, Any]:
    """Serialize one content-free terminal record for public projections."""
    identity = record.identity
    return {
        "id": record.id,
        "owner_id": identity.owner_id,
        "workspace_id": identity.workspace_id,
        "session_id": identity.session_id,
        "terminal_name": identity.terminal_name,
        "native_harness_id": identity.native_harness_id,
        "native_session_id": identity.native_session_id,
        "command_digest": identity.command_digest,
        "cwd_digest": identity.cwd_digest,
        "executable_path_digest": identity.executable_path_digest,
        "executable_version": identity.executable_version,
        "state": record.state.value,
        "revision": record.revision,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_attached_at": record.last_attached_at,
        "last_liveness_at": record.last_liveness_at,
    }


def _validate_identity(value: str, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is invalid")
    text = value.strip()
    if not text or text != value or len(text) > MAX_TERMINAL_IDENTITY_CHARS:
        raise ValueError(f"{field_name} is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError(f"{field_name} is invalid")


def _validate_digest(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a SHA-256 digest")


def _validate_timestamp(value: str, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
