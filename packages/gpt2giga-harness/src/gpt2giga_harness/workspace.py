"""Compatibility alias for workspace discovery."""

import sys

from gpt2giga_harness.projects.workspace import api as _implementation

sys.modules[__name__] = _implementation
