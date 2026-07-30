"""Compatibility alias for the review bounded context."""

import sys

from gpt2giga_harness.review import support as _implementation

sys.modules[__name__] = _implementation
