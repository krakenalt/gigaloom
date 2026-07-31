"""Compatibility alias for the regrouped harness implementation."""

from __future__ import annotations

import sys

from gigaloom.harnesses.builtins.direct_chat import adapter as _implementation

sys.modules[__name__] = _implementation
