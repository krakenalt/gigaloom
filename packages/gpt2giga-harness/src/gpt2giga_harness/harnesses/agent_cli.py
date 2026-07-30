"""Compatibility alias for the regrouped harness implementation."""

from __future__ import annotations

import sys

from gpt2giga_harness.harnesses.sdk import agent_cli as _implementation

sys.modules[__name__] = _implementation
