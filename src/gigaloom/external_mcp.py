"""Compatibility alias for :mod:`gigaloom.tools.mcp.external`."""

import sys

from .tools.mcp import external as _implementation

sys.modules[__name__] = _implementation
