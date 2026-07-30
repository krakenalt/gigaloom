"""Compatibility alias for :mod:`gigaloom.diagnostics.performance.runtime`."""

import sys as _sys

from .diagnostics.performance import runtime as _implementation

_sys.modules[__name__] = _implementation
