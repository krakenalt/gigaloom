"""Compatibility alias for the review bounded context."""

import sys

from gigaloom.review import provenance as _implementation

sys.modules[__name__] = _implementation
