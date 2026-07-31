"""Compatibility alias for workspace discovery."""

import sys

from gigaloom.projects.workspace import api as _implementation

sys.modules[__name__] = _implementation
