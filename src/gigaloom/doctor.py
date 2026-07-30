"""Compatibility alias for :mod:`gigaloom.diagnostics.doctor.report`."""

import sys as _sys

from .diagnostics.doctor import report as _implementation

_sys.modules[__name__] = _implementation
