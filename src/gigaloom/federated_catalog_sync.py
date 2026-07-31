"""Compatibility alias for federated catalog synchronization."""

import sys as _sys

from .integrations.catalog import sync as _implementation

_sys.modules[__name__] = _implementation
