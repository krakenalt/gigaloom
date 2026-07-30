"""Compatibility alias for :mod:`gpt2giga_harness.skills.catalog_proxy.server`."""

import sys

from .skills.catalog_proxy import server as _implementation

sys.modules[__name__] = _implementation
