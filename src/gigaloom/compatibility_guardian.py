"""Compatibility alias for :mod:`gigaloom.diagnostics.compatibility.guardian`."""

import sys as _sys

from .diagnostics.compatibility import guardian as _implementation

_sys.modules[__name__] = _implementation
