"""Compatibility alias for :mod:`gigaloom.tools.mcp.managed`."""

import sys

from .tools.mcp import managed as _implementation

sys.modules[__name__] = _implementation
