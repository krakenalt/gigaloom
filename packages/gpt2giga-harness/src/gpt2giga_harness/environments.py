"""Compatibility alias for environment capture."""

import sys

from gpt2giga_harness.projects.environment import registry as _implementation

sys.modules[__name__] = _implementation
