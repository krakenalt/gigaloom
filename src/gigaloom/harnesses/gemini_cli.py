"""Compatibility alias for the provider-grouped Gemini adapter."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from gigaloom.harnesses.builtins.gemini import cli as _implementation

if TYPE_CHECKING:
    from gigaloom.runtime.api import (  # noqa: F401
        ApprovalStatus,
        EnforcementLevel,
        PermissionAction,
        PolicyContext,
        PolicyDecision,
        PolicyResolution,
        RuntimeCoordinationStore,
    )

sys.modules[__name__] = _implementation
