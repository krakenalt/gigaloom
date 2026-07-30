"""Compatibility alias for :mod:`gigaloom.automation.agents.api`."""

import sys as _sys

from .automation.agents import api as _implementation

_sys.modules[__name__] = _implementation
