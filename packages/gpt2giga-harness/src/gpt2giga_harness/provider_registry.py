"""Compatibility alias for :mod:`gpt2giga_harness.providers.registry`."""

import sys as _sys

from .providers import registry as _implementation

_sys.modules[__name__] = _implementation
