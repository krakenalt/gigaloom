"""Compatibility alias for :mod:`gigaloom.providers.migration`."""

import sys as _sys

from .providers import migration as _implementation

_sys.modules[__name__] = _implementation
