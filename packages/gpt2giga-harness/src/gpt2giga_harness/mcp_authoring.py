"""Compatibility alias for :mod:`gpt2giga_harness.tools.mcp.authoring`."""

import sys

from .tools.mcp import authoring as _implementation

sys.modules[__name__] = _implementation
