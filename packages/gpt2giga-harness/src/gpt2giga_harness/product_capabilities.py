"""Compatibility alias for :mod:`gpt2giga_harness.diagnostics.inventory.capabilities`."""

import sys as _sys

from .diagnostics.inventory import capabilities as _implementation

_sys.modules[__name__] = _implementation
