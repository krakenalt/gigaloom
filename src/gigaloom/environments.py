"""Compatibility alias for environment capture."""

import sys

from gigaloom.projects.environment import registry as _implementation

sys.modules[__name__] = _implementation
