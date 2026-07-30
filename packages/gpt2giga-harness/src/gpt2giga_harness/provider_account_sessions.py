"""Compatibility alias for provider account session binding."""

import sys as _sys

from .providers.accounts import sessions as _implementation

_sys.modules[__name__] = _implementation
