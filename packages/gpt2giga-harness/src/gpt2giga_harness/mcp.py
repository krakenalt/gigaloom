"""Compatibility alias for :mod:`gpt2giga_harness.tools.mcp.api`."""

import sys

from .tools.mcp import api as _implementation

sys.modules[__name__] = _implementation
