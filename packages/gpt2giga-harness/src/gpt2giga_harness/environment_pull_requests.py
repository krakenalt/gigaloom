"""Compatibility alias for governed environment pull-request actions."""

import sys

from gpt2giga_harness.projects.environment import pull_request as _implementation

sys.modules[__name__] = _implementation
