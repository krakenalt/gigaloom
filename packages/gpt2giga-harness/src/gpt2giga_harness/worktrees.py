"""Compatibility alias for isolated project worktrees."""

import sys

from gpt2giga_harness.projects.workspace import worktrees as _implementation

sys.modules[__name__] = _implementation
