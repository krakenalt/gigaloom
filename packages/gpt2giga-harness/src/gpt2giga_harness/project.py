"""Compatibility alias for the project bounded context."""

import sys

from gpt2giga_harness.projects import api as _implementation

sys.modules[__name__] = _implementation
