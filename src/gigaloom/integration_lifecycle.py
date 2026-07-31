"""Compatibility alias for the integration lifecycle service."""

import sys as _sys

from .integrations.packages import lifecycle as _implementation

_sys.modules[__name__] = _implementation
