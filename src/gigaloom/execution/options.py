"""Typed options resolved for one Harness session run."""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
from typing import Any, ClassVar

from gigaloom.execution import ExecutionTransport
from gigaloom.native import HarnessInvocationMode
from gigaloom.projects.api import WorkspacePolicy
from gigaloom.types import (
    GigaChatApiMode,
    GigaChatBuiltinTool,
    HarnessCapability,
)


@dataclass(slots=True)
class RunOptions(MutableMapping[str, Any]):
    """Normalized runner inputs with a temporary mapping compatibility view."""

    prompt: str
    harness_id: str
    harness_kind: str
    model: str | None
    api_mode: GigaChatApiMode
    builtin_tools: tuple[GigaChatBuiltinTool, ...]
    capability: HarnessCapability
    mode: str
    invocation_mode: HarnessInvocationMode
    execution_transport: ExecutionTransport | None
    workspace: str | None
    stream: bool
    extra: dict[str, Any]
    native_session_id: str | None
    attachment_ids: tuple[str, ...]
    workspace_policy: WorkspacePolicy
    permission_profile: str
    permission_origin: str
    required_permission_actions: tuple[Any, ...]
    agent_id: str | None
    agent_profile_snapshot: dict[str, Any] | None
    agent_execution_plan: dict[str, Any] | None

    _FIELD_NAMES: ClassVar[tuple[str, ...]] = (
        "prompt",
        "harness_id",
        "harness_kind",
        "model",
        "api_mode",
        "builtin_tools",
        "capability",
        "mode",
        "invocation_mode",
        "execution_transport",
        "workspace",
        "stream",
        "extra",
        "native_session_id",
        "attachment_ids",
        "workspace_policy",
        "permission_profile",
        "permission_origin",
        "required_permission_actions",
        "agent_id",
        "agent_profile_snapshot",
        "agent_execution_plan",
    )

    def __getitem__(self, key: str) -> Any:
        if key not in self._FIELD_NAMES:
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._FIELD_NAMES:
            raise KeyError(key)
        setattr(self, key, value)

    def __delitem__(self, key: str) -> None:
        raise TypeError("run options fields cannot be deleted")

    def __iter__(self) -> Iterator[str]:
        return iter(self._FIELD_NAMES)

    def __len__(self) -> int:
        return len(self._FIELD_NAMES)
