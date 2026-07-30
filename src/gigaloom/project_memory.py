"""Compatibility alias for project memory."""

import sys

from gigaloom.projects import memory as _implementation

sys.modules[__name__] = _implementation
