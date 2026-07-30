"""Compatibility alias for provider account authentication."""

import sys as _sys

from .providers.accounts import broker as _implementation

_sys.modules[__name__] = _implementation
