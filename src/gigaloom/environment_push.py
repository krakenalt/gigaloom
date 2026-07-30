"""Compatibility alias for governed environment push actions."""

import sys

from gigaloom.projects.environment import push as _implementation

sys.modules[__name__] = _implementation
