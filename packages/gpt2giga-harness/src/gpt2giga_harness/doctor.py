"""Compatibility alias for :mod:`gpt2giga_harness.diagnostics.doctor.report`."""

import sys as _sys

from .diagnostics.doctor import report as _implementation

_sys.modules[__name__] = _implementation
