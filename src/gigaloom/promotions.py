"""Compatibility alias for the review bounded context."""

import sys

from gigaloom.review.promotions import api as _implementation

sys.modules[__name__] = _implementation
