"""Compatibility alias for :mod:`gpt2giga_harness.automation.arena.api`."""

import sys as _sys

from .automation.arena import api as _implementation

_sys.modules[__name__] = _implementation
