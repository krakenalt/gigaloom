"""Compatibility alias for the canonical diagnostics performance workload."""

import sys as _sys
from importlib import import_module as _import_module

_implementation = _import_module(
    "gpt2giga_harness.diagnostics.performance.workloads.surfaces.tui_render"
)
_sys.modules[__name__] = _implementation
