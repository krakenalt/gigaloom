"""Compatibility alias for governed environment push actions."""

import sys

from gpt2giga_harness.projects.environment import push as _implementation

sys.modules[__name__] = _implementation
