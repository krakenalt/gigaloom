"""Compatibility alias for the provider-grouped Gemini adapter."""

from __future__ import annotations

import sys

from gpt2giga_harness.runtime.models import ApprovalStatus  # noqa: F401
from gpt2giga_harness.runtime.policy import (  # noqa: F401
    EnforcementLevel,
    PermissionAction,
    PolicyContext,
    PolicyDecision,
    PolicyResolution,
)
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore  # noqa: F401
from gpt2giga_harness.harnesses.builtins.gemini import cli as _implementation

sys.modules[__name__] = _implementation
