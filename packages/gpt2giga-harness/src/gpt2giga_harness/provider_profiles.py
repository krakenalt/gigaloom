"""Compatibility alias for :mod:`gpt2giga_harness.providers.profiles`."""

import sys as _sys

from .providers import profiles as _implementation

_sys.modules[__name__] = _implementation
