"""Compatibility alias for :mod:`gigaloom.tools.mcp.managed`."""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tools.mcp.managed import write_startup_config as write_startup_config
else:
    from .tools.mcp import managed as _implementation

    sys.modules[__name__] = _implementation
