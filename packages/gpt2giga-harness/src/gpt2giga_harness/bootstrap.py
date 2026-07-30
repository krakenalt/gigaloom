"""Compatibility alias for project bootstrap."""

import sys

from gpt2giga_harness.projects import bootstrap as _implementation

sys.modules[__name__] = _implementation
