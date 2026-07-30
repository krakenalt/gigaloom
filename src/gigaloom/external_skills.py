"""Compatibility alias for :mod:`gigaloom.skills.external`."""

import sys

from .skills import external as _implementation

sys.modules[__name__] = _implementation
