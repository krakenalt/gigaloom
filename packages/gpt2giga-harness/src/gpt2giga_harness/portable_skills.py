"""Compatibility alias for :mod:`gpt2giga_harness.skills.portable`."""

import sys

from .skills import portable as _implementation

sys.modules[__name__] = _implementation
