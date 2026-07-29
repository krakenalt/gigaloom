"""Compatibility alias for :mod:`gpt2giga_harness.providers.settings`."""

import sys as _sys

from .providers import settings as _implementation

_sys.modules[__name__] = _implementation
