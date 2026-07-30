"""Public MCP contracts, inventory, discovery, and history facade."""

import sys

from . import probe as _implementation
from .history import MCPProbeHistoryStore
from .inventory import build_mcp_inventory, descriptor_from_profile
from .managed import HeadlessManagedMCPSnapshotStore

_implementation.MCPProbeHistoryStore = MCPProbeHistoryStore
_implementation.HeadlessManagedMCPSnapshotStore = HeadlessManagedMCPSnapshotStore
_implementation.build_mcp_inventory = build_mcp_inventory
_implementation.descriptor_from_profile = descriptor_from_profile

sys.modules[__name__] = _implementation
