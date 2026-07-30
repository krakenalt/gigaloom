"""Compatibility alias for the provider-grouped implementation."""

from __future__ import annotations

import sys

from gigaloom.harnesses.builtins.claude import target as _implementation

sys.modules[__name__] = _implementation
