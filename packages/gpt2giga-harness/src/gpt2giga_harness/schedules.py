"""Compatibility alias for :mod:`gpt2giga_harness.automation.schedules.api`."""

import sys as _sys

from .automation.schedules import api as _implementation

_sys.modules[__name__] = _implementation
