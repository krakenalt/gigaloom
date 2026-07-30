"""Compatibility alias for :mod:`gpt2giga_harness.diagnostics.performance.runtime`."""

import sys as _sys

from .diagnostics.performance import runtime as _implementation

_sys.modules[__name__] = _implementation
