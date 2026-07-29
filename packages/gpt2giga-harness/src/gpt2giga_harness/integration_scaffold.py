"""Compatibility alias for Integration SDK scaffolding."""

import sys as _sys

from .integrations.sdk import scaffold as _implementation

_sys.modules[__name__] = _implementation
