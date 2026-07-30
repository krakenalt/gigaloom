"""Compatibility alias for :mod:`gigaloom.tools.mcp.api`."""

import sys

from .tools.mcp import api as _implementation

sys.modules[__name__] = _implementation
