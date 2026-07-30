"""Compatibility alias for :mod:`gpt2giga_harness.automation.workflows.api`."""

import sys as _sys

from .automation.workflows import api as _implementation

_sys.modules[__name__] = _implementation
