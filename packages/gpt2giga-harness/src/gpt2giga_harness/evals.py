"""Compatibility alias for :mod:`gpt2giga_harness.automation.evaluations.api`."""

import sys as _sys

from .automation.evaluations import api as _implementation

_sys.modules[__name__] = _implementation
