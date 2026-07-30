"""Compatibility alias for :mod:`gigaloom.providers.profiles`."""

import sys as _sys

from .providers import profiles as _implementation

_sys.modules[__name__] = _implementation
