"""Compatibility alias for the review bounded context."""

import sys

from gpt2giga_harness.review import evidence as _implementation

sys.modules[__name__] = _implementation
