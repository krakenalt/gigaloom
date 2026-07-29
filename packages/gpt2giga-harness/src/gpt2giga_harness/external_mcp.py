"""Compatibility alias for :mod:`gpt2giga_harness.tools.mcp.external`."""

import sys

from .tools.mcp import external as _implementation

sys.modules[__name__] = _implementation
