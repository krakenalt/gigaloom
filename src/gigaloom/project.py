"""Compatibility alias for the project bounded context."""

import sys

from gigaloom.projects import api as _implementation

sys.modules[__name__] = _implementation
