"""Durable attach and truthful resume contracts for native Codex."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Any

from gigaloom.native.terminal import TerminalAccess, TerminalRecord


CODEX_SESSION_BINDING_SCHEMA_VERSION = 1
_IDENTITY = re.compile(r"[A-Za-z0-9._:@+~-]{1,256}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class CodexResumeMode(str, Enum):
    """Observed attach or resume behavior."""

    LIVE_REATTACH = "live_reattach"
    COLD_RESUME = "cold_resume"
    FRESH = "fresh"
    BLOCKED = "blocked"


class CodexCwdDecision(str, Enum):
    """Explicit operator choice for a changed workspace binding."""

    BOUND = "bound"
    CURRENT = "current"


@dataclass(frozen=True)
class CodexSessionBinding:
    """Private durable binding between GigaLoom, terminal, cwd, and Codex."""

    id: str
    access: TerminalAccess
    terminal_id: str
    terminal_revision: int
    cwd: str
    cwd_digest: str
    web_url: str
    generation: int
    created_at: str
    updated_at: str
    native_thread_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.access, TerminalAccess):
            raise ValueError("Codex binding access is invalid")
        for value, label in (
            (self.id, "binding id"),
            (self.terminal_id, "terminal id"),
        ):
            if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
                raise ValueError(f"Codex {label} is invalid")
        if self.native_thread_id is not None and (
            not isinstance(self.native_thread_id, str)
            or _IDENTITY.fullmatch(self.native_thread_id) is None
        ):
            raise ValueError("Codex native thread id is invalid")
        if (
            isinstance(self.terminal_revision, bool)
            or not isinstance(self.terminal_revision, int)
            or self.terminal_revision < 1
        ):
            raise ValueError("Codex terminal revision is invalid")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 1
        ):
            raise ValueError("Codex binding generation is invalid")
        if not isinstance(self.cwd, str) or not self.cwd:
            raise ValueError("Codex binding cwd is invalid")
        if (
            not isinstance(self.cwd_digest, str)
            or _SHA256.fullmatch(self.cwd_digest) is None
        ):
            raise ValueError("Codex binding cwd digest is invalid")
        if not isinstance(self.web_url, str) or not self.web_url:
            raise ValueError("Codex binding Web URL is invalid")
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)


@dataclass(frozen=True)
class CodexResumeOutcome:
    """Content-free operator result for attach or cold resume."""

    mode: CodexResumeMode
    binding_id: str
    terminal: TerminalRecord | None
    web_url: str
    resume_hint: tuple[str, ...]
    message: str
    reason_code: str


def codex_session_binding_to_dict(
    binding: CodexSessionBinding,
) -> dict[str, Any]:
    """Serialize a public binding without raw provider thread identity or cwd."""
    return {
        "schema_version": CODEX_SESSION_BINDING_SCHEMA_VERSION,
        "id": binding.id,
        "owner_id": binding.access.owner_id,
        "workspace_id": binding.access.workspace_id,
        "session_id": binding.access.session_id,
        "terminal_id": binding.terminal_id,
        "terminal_revision": binding.terminal_revision,
        "cwd_digest": binding.cwd_digest,
        "web_url": binding.web_url,
        "generation": binding.generation,
        "created_at": binding.created_at,
        "updated_at": binding.updated_at,
        "has_native_thread_id": binding.native_thread_id is not None,
    }


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("Codex binding timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Codex binding timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Codex binding timestamp must include a timezone")
