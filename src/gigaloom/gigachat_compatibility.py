"""Compatibility alias for GigaChat gateway compatibility evidence."""

import sys as _sys

from .providers.protocols.gigachat import compatibility as _implementation

_sys.modules[__name__] = _implementation
