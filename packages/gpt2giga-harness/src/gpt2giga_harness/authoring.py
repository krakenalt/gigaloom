"""Compatibility alias for :mod:`gpt2giga_harness.automation.agents.authoring`."""

import sys as _sys

from .automation.agents import authoring as _implementation

_sys.modules[__name__] = _implementation
