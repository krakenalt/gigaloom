"""Models for the arena subcontext."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from gigaloom.execution import ExecutionTransport
from gigaloom.types import GigaChatApiMode


class ArenaNotFoundError(KeyError):
    """Raised when an arena run does not exist."""


class ArenaReviewConflictError(ValueError):
    """Raised when reviewed Arena evidence no longer matches current runs."""


@dataclass(frozen=True)
class HarnessArenaRequest:
    """Request to compare several harnesses on the same prompt."""

    prompt: str
    harness_ids: tuple[str, ...]
    model: str | None = None
    api_mode: GigaChatApiMode = GigaChatApiMode.V2
    mode: str = "plan"
    workspace: str | None = None
    attachment_ids: tuple[str, ...] = ()
    workspace_policy: str = "auto"
    execution_transport: ExecutionTransport | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessArenaChildRun:
    """One child run inside an arena comparison."""

    harness_id: str
    index: int
    session_id: str | None
    run_id: str | None
    status: str
    error: str | None = None
    result_text: str | None = None


@dataclass(frozen=True)
class HarnessArenaRun:
    """Persisted arena parent object linking child runs."""

    id: str
    session_id: str
    status: str
    prompt: str
    harness_ids: tuple[str, ...]
    model: str | None
    api_mode: GigaChatApiMode
    mode: str
    workspace: str | None
    attachment_ids: tuple[str, ...]
    workspace_policy: str
    execution_transport: ExecutionTransport | None
    created_at: str
    updated_at: str
    child_runs: tuple[HarnessArenaChildRun, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
