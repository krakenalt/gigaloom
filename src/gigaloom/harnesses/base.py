"""Compatibility alias for the regrouped harness implementation."""

from __future__ import annotations

import sys

from gigaloom.harnesses.sdk import base as _implementation

sys.modules[__name__] = _implementation
