"""Compatibility alias for :mod:`gigaloom.diagnostics.performance.baseline`."""

import sys as _sys

from .diagnostics.performance import baseline as _implementation

_sys.modules[__name__] = _implementation
