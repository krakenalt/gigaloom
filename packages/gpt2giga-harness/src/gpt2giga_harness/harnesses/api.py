"""Public facade for native consumers of harness adapter utilities."""

from __future__ import annotations

from importlib import import_module
from typing import Any

attachment_raw_metadata: Any
build_safe_env: Any
claude_code_custom_headers: Any
cli_args_from_attachments: Any
gemini_cli_custom_headers: Any
prompt_with_attachments: Any

__all__ = [
    "attachment_raw_metadata",
    "build_safe_env",
    "claude_code_custom_headers",
    "cli_args_from_attachments",
    "gemini_cli_custom_headers",
    "prompt_with_attachments",
]

_LAZY_EXPORTS = {
    "attachment_raw_metadata": (
        "gpt2giga_harness.harnesses.attachment_plan",
        "attachment_raw_metadata",
    ),
    "build_safe_env": (
        "gpt2giga_harness.harnesses.agent_cli",
        "build_safe_env",
    ),
    "claude_code_custom_headers": (
        "gpt2giga_harness.harnesses.claude_code",
        "claude_code_custom_headers",
    ),
    "cli_args_from_attachments": (
        "gpt2giga_harness.harnesses.attachment_plan",
        "cli_args_from_attachments",
    ),
    "gemini_cli_custom_headers": (
        "gpt2giga_harness.harnesses.gemini_cli",
        "gemini_cli_custom_headers",
    ),
    "prompt_with_attachments": (
        "gpt2giga_harness.harnesses.attachment_plan",
        "prompt_with_attachments",
    ),
}


def __getattr__(name: str) -> Any:
    """Resolve one compatibility-stable adapter utility lazily."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
