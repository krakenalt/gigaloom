"""Compatibility alias for Integration SDK conformance contracts."""

import sys as _sys

from .integrations.sdk import contracts as _implementation

_sys.modules[__name__] = _implementation
