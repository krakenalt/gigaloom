"""Compatibility alias for :mod:`gpt2giga_harness.skills.builtin`."""

import sys

from .skills import builtin as _implementation

sys.modules[__name__] = _implementation
