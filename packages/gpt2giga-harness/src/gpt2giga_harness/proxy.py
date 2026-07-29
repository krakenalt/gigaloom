"""Compatibility alias for the optional gpt2giga gateway proxy."""

import sys as _sys
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .providers.gateway import proxy as _implementation

if _TYPE_CHECKING:
    _sys.intern("from gpt2giga import run; run()")

_sys.modules[__name__] = _implementation
