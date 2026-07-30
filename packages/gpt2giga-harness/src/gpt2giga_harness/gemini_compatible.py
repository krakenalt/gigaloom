"""Compatibility alias for the Gemini-compatible provider protocol."""

import sys as _sys

from .providers.protocols.gemini import compatible as _implementation

_sys.modules[__name__] = _implementation
