"""Compatibility alias for :mod:`gpt2giga_harness.integrations.catalog.api`."""

import sys as _sys

from .integrations.catalog import api as _implementation

_sys.modules[__name__] = _implementation
