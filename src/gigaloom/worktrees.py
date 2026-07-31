"""Compatibility alias for isolated project worktrees."""

import sys

from gigaloom.projects.workspace import worktrees as _implementation

sys.modules[__name__] = _implementation
