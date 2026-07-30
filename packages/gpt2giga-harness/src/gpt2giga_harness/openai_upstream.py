"""Compatibility alias for the OpenAI-compatible upstream adapter."""

from importlib import import_module
import sys as _sys
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .providers.protocols.openai import upstream as _implementation

if _TYPE_CHECKING:
    import_module("gpt2giga.providers.openai_compatible")

_sys.modules[__name__] = _implementation
