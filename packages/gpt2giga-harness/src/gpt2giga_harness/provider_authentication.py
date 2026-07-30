"""Compatibility alias for provider authentication evidence."""

import sys as _sys

from .providers.authentication import capabilities as _implementation

_sys.modules[__name__] = _implementation
