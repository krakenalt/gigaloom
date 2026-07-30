"""Compatibility alias for :mod:`gigaloom.automation.attention.service`."""

import sys as _sys

from .automation.attention import service as _implementation

_sys.modules[__name__] = _implementation
