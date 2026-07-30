"""Compatibility alias for the provider-grouped Gemini adapter."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from gpt2giga_harness.harnesses.builtins.gemini import cli as _implementation

if TYPE_CHECKING:
    from gpt2giga_harness.runtime.api import (  # noqa: F401
        ApprovalStatus,
        EnforcementLevel,
        PermissionAction,
        PolicyContext,
        PolicyDecision,
        PolicyResolution,
        RuntimeCoordinationStore,
    )

sys.modules[__name__] = _implementation
