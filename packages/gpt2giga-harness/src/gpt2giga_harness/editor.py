"""Compatibility alias for safe editor planning."""

import sys

from gpt2giga_harness.projects.environment import editor as _implementation

sys.modules[__name__] = _implementation
