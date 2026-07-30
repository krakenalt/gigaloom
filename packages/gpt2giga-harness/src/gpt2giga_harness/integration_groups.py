"""Compatibility alias for grouped integration transactions."""

import sys as _sys

from .integrations.groups import service as _implementation

_sys.modules[__name__] = _implementation
