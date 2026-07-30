"""Compatibility alias for the provider-grouped implementation."""

from __future__ import annotations

import sys

from gpt2giga_harness.harnesses.builtins.gemini import acp as _implementation

sys.modules[__name__] = _implementation
