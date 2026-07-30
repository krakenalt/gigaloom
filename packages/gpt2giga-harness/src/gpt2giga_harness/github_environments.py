"""Compatibility alias for GitHub environment enrichment."""

import sys

from gpt2giga_harness.projects.environment import github as _implementation

sys.modules[__name__] = _implementation
