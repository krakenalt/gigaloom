"""Compatibility alias for :mod:`gigaloom.diagnostics.inventory.capabilities`."""

import sys as _sys

from .diagnostics.inventory import capabilities as _implementation

_sys.modules[__name__] = _implementation
