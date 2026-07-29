"""Compatibility alias for :mod:`gpt2giga_harness.tools.mcp.managed_inventory`."""

import sys

from .tools.mcp import managed_inventory as _implementation

sys.modules[__name__] = _implementation
