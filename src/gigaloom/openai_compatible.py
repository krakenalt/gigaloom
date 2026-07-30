"""Compatibility alias for the OpenAI-compatible provider protocol."""

import sys as _sys

from .providers.protocols.openai import compatible as _implementation

_sys.modules[__name__] = _implementation
