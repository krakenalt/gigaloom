"""Compatibility alias for :mod:`gigaloom.automation.schedules.api`."""

import sys as _sys

from .automation.schedules import api as _implementation

_sys.modules[__name__] = _implementation
