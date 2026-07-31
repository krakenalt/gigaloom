"""Compatibility alias for safe editor planning."""

import sys

from gigaloom.projects.environment import editor as _implementation

sys.modules[__name__] = _implementation
