"""Compatibility alias for the federated integration catalog."""

import sys as _sys

from .integrations.catalog import federated_api as _implementation

_sys.modules[__name__] = _implementation
