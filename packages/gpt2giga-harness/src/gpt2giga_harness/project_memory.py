"""Compatibility alias for project memory."""

import sys

from gpt2giga_harness.projects import memory as _implementation

sys.modules[__name__] = _implementation
