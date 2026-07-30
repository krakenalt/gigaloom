"""Compatibility alias for :mod:`gigaloom.diagnostics.inventory.product`."""

import sys as _sys

from .diagnostics.inventory import product as _implementation

_sys.modules[__name__] = _implementation
