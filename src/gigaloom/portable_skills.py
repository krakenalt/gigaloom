"""Compatibility alias for :mod:`gigaloom.skills.portable`."""

import sys

from .skills import portable as _implementation

sys.modules[__name__] = _implementation
