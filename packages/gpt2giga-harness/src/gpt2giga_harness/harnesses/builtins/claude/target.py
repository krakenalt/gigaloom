"""Claude provider-specific target facade."""

from __future__ import annotations

from gpt2giga_harness.harnesses.builtins.claude.target_contracts import (
    CLAUDE_PLUGIN_TARGET_DESCRIPTOR,
    CLAUDE_PLUGIN_TARGET_ID,
    CLAUDE_PLUGIN_TARGET_REVISION,
    ClaudePluginApproval,
    ClaudePluginCommandError,
    ClaudePluginCommandResult,
    ClaudePluginHandoff,
    ClaudePluginHealth,
    ClaudePluginInstallation,
    ClaudePluginPlan,
    ClaudePluginPolicyError,
    ClaudePluginProbe,
    ClaudePluginRequest,
    ClaudePluginResult,
    ClaudePluginSource,
    ClaudePluginSourceKind,
    ClaudePluginTargetError,
)
from gpt2giga_harness.harnesses.builtins.claude.target_driver import (
    ClaudePluginTargetDriver,
)
from gpt2giga_harness.harnesses.builtins.claude.target_source import (
    claude_plugin_source_checksum,
    claude_plugin_target_plugin,
)

__all__ = [
    "CLAUDE_PLUGIN_TARGET_DESCRIPTOR",
    "CLAUDE_PLUGIN_TARGET_ID",
    "CLAUDE_PLUGIN_TARGET_REVISION",
    "ClaudePluginApproval",
    "ClaudePluginCommandError",
    "ClaudePluginCommandResult",
    "ClaudePluginHandoff",
    "ClaudePluginHealth",
    "ClaudePluginInstallation",
    "ClaudePluginPlan",
    "ClaudePluginPolicyError",
    "ClaudePluginProbe",
    "ClaudePluginRequest",
    "ClaudePluginResult",
    "ClaudePluginSource",
    "ClaudePluginSourceKind",
    "ClaudePluginTargetDriver",
    "ClaudePluginTargetError",
    "claude_plugin_source_checksum",
    "claude_plugin_target_plugin",
]
