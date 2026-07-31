"""Public facade for cross-context native operator primitives."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from gigaloom.native.launch import (
    AgentResolutionKind,
    AgentResolutionReason,
    AgentResolutionResult,
    NativeAgentLaunchSpec,
    NativeIntentMatcher,
    NativeIntentMatcherKind,
    NativeInvocation,
    NativeLaunchMode,
    NativeLaunchPlan,
    NativeLaunchReason,
    TerminalContext,
    plan_native_launch,
)

CodexStdioJsonRpcClient: Any

__all__ = [
    "AgentResolutionKind",
    "AgentResolutionReason",
    "AgentResolutionResult",
    "CodexStdioJsonRpcClient",
    "NativeAgentLaunchSpec",
    "NativeIntentMatcher",
    "NativeIntentMatcherKind",
    "NativeInvocation",
    "NativeLaunchMode",
    "NativeLaunchPlan",
    "NativeLaunchReason",
    "TerminalContext",
    "plan_native_launch",
]


def __getattr__(name: str) -> Any:
    """Resolve compatibility-stable operator clients lazily."""
    if name != "CodexStdioJsonRpcClient":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(
        import_module("gigaloom.native.codex_operator.protocol"),
        name,
    )
    globals()[name] = value
    return value
