"""Compatibility alias for :mod:`gpt2giga_harness.automation.workflows.catalog`."""

import sys as _sys

from .automation.workflows import catalog as _implementation

_sys.modules[__name__] = _implementation
