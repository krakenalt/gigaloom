"""Compatibility alias for governed environment commit actions."""

import sys

from gigaloom.projects.environment import commit as _implementation

sys.modules[__name__] = _implementation
