"""Gemini provider-specific target facade."""

from __future__ import annotations

from gpt2giga_harness.harnesses.builtins.gemini.target_contracts import (
    GEMINI_EXTENSION_TARGET_DESCRIPTOR,
    GEMINI_EXTENSION_TARGET_ID,
    GEMINI_EXTENSION_TARGET_REVISION,
    GeminiExtensionApproval,
    GeminiExtensionCommandError,
    GeminiExtensionCommandResult,
    GeminiExtensionHandoff,
    GeminiExtensionHealth,
    GeminiExtensionInstallation,
    GeminiExtensionPlan,
    GeminiExtensionPolicyError,
    GeminiExtensionProbe,
    GeminiExtensionRequest,
    GeminiExtensionResult,
    GeminiExtensionSource,
    GeminiExtensionSourceKind,
    GeminiExtensionTargetError,
)
from gpt2giga_harness.harnesses.builtins.gemini.target_driver import (
    GeminiExtensionTargetDriver,
)
from gpt2giga_harness.harnesses.builtins.gemini.target_source import (
    gemini_extension_source_checksum,
    gemini_extension_target_plugin,
)

__all__ = [
    "GEMINI_EXTENSION_TARGET_DESCRIPTOR",
    "GEMINI_EXTENSION_TARGET_ID",
    "GEMINI_EXTENSION_TARGET_REVISION",
    "GeminiExtensionApproval",
    "GeminiExtensionCommandError",
    "GeminiExtensionCommandResult",
    "GeminiExtensionHandoff",
    "GeminiExtensionHealth",
    "GeminiExtensionInstallation",
    "GeminiExtensionPlan",
    "GeminiExtensionPolicyError",
    "GeminiExtensionProbe",
    "GeminiExtensionRequest",
    "GeminiExtensionResult",
    "GeminiExtensionSource",
    "GeminiExtensionSourceKind",
    "GeminiExtensionTargetDriver",
    "GeminiExtensionTargetError",
    "gemini_extension_source_checksum",
    "gemini_extension_target_plugin",
]
