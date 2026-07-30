"""Stable provider-neutral execution axes."""

from __future__ import annotations

from enum import Enum


class ExecutionTransport(str, Enum):
    """Describe the effective transport used for one execution."""

    NATIVE_STRUCTURED = "native_structured"
    NATIVE_TERMINAL = "native_terminal"
    ONE_SHOT = "one_shot"


class HarnessInvocationMode(str, Enum):
    """Describe how a harness should be invoked."""

    HEADLESS = "headless"
    NATIVE = "native"


ExecutionTransport.__module__ = "gpt2giga_harness.execution"
HarnessInvocationMode.__module__ = "gpt2giga_harness.native.models"

__all__ = ["ExecutionTransport", "HarnessInvocationMode"]
