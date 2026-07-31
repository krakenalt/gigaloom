"""Compatibility alias for the regrouped harness implementation."""

from __future__ import annotations

import sys

from gigaloom.harnesses.builtins.gemini import workbench as _implementation

sys.modules[__name__] = _implementation
