"""Compatibility alias for integration package contracts and discovery."""

import sys as _sys

from .integrations.packages import registry as _implementation

_sys.modules[__name__] = _implementation
