"""Compatibility alias for :mod:`gpt2giga_harness.diagnostics.performance.baseline`."""

import sys as _sys

from .diagnostics.performance import baseline as _implementation

_sys.modules[__name__] = _implementation
