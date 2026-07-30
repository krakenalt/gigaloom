"""Compatibility alias for :mod:`gigaloom.providers.settings`."""

import sys as _sys

from .providers import settings as _implementation

_sys.modules[__name__] = _implementation
