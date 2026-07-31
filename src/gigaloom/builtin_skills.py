"""Compatibility alias for :mod:`gigaloom.skills.builtin`."""

import sys

from .skills import builtin as _implementation

sys.modules[__name__] = _implementation
