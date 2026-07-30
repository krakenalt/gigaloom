"""Compatibility alias for project bootstrap."""

import sys

from gigaloom.projects import bootstrap as _implementation

sys.modules[__name__] = _implementation
