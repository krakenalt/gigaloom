"""Compatibility alias for the transactional integration installer."""

import sys as _sys

from .integrations.packages import installer as _implementation

_sys.modules[__name__] = _implementation
