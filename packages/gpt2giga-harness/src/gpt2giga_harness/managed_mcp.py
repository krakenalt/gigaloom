"""Compatibility alias for :mod:`gpt2giga_harness.tools.mcp.managed`."""

import sys

from .tools.mcp import managed as _implementation

sys.modules[__name__] = _implementation
