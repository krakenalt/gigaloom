"""Compatibility alias for integration runtime snapshots and activation."""

import sys as _sys

from .integrations.packages import runtime as _implementation

_sys.modules[__name__] = _implementation
