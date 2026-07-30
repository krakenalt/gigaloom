"""Compatibility alias for governed environment commit actions."""

import sys

from gpt2giga_harness.projects.environment import commit as _implementation

sys.modules[__name__] = _implementation
