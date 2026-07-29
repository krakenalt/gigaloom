"""Compatibility alias for the Anthropic-compatible provider protocol."""

import sys as _sys

from .providers.protocols.anthropic import compatible as _implementation

_sys.modules[__name__] = _implementation
