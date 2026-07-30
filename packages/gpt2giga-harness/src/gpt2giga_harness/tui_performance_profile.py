"""Compatibility alias for :mod:`gpt2giga_harness.diagnostics.performance.tui`."""

import sys as _sys

from .diagnostics.performance import tui as _implementation

_sys.modules[__name__] = _implementation
