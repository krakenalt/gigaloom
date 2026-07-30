"""Compatibility alias for :mod:`gpt2giga_harness.skills.external`."""

import sys

from .skills import external as _implementation

sys.modules[__name__] = _implementation
