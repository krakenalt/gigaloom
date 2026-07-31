"""Built-in Unified Harness implementations."""

from importlib import import_module
from typing import Any

__all__ = [
    "ClaudeCodeHarness",
    "CodexCliHarness",
    "DirectChatHarness",
    "EchoHarness",
    "GeminiCliHarness",
]

_LAZY_EXPORTS = {
    "ClaudeCodeHarness": (
        "gigaloom.harnesses.claude_code",
        "ClaudeCodeHarness",
    ),
    "CodexCliHarness": ("gigaloom.harnesses.codex_cli", "CodexCliHarness"),
    "DirectChatHarness": (
        "gigaloom.harnesses.direct_chat",
        "DirectChatHarness",
    ),
    "EchoHarness": ("gigaloom.harnesses.echo", "EchoHarness"),
    "GeminiCliHarness": (
        "gigaloom.harnesses.gemini_cli",
        "GeminiCliHarness",
    ),
}


def __getattr__(name: str) -> Any:
    """Load concrete adapters only when callers request them."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
