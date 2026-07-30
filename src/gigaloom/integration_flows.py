"""Compatibility alias for application-owned integration flows."""

import sys as _sys

from .integrations.flows import service as _implementation

_sys.modules[__name__] = _implementation
