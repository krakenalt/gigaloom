"""Compatibility alias for the optional gpt2giga gateway preset."""

from importlib import import_module
import sys as _sys
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .providers.gateway import preset as _implementation

if _TYPE_CHECKING:
    import_module("gpt2giga.cli")

_sys.modules[__name__] = _implementation
