"""Compatibility alias for :mod:`gigaloom.tools.mcp.managed_inventory`."""

import sys

from .tools.mcp import managed_inventory as _implementation

sys.modules[__name__] = _implementation
