"""Compatibility alias for :mod:`gpt2giga_harness.skills.library`."""

import sys

from .skills import library as _implementation

sys.modules[__name__] = _implementation
