"""Compatibility alias for :mod:`gigaloom.skills.library`."""

import sys

from .skills import library as _implementation

sys.modules[__name__] = _implementation
