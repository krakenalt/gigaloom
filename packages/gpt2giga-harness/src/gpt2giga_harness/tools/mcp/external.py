"""Public reviewed external MCP normalization and target facade."""

from .external_models import (
    HARNESS_MANAGED_MCP_TARGET_ID,
    ExternalMCPArtifactResolution,
    ExternalMCPDescriptor,
    ExternalMCPSelection,
    ExternalMCPSelectionKind,
    ExternalMCPTargetPreview,
    ExternalMCPToolPolicy,
    external_mcp_descriptor_to_dict,
    external_mcp_selection_from_dict,
)
from .external_transport import (
    external_mcp_server_spec,
    normalize_external_mcp_candidate,
    project_external_mcp_target,
)

__all__ = [
    "HARNESS_MANAGED_MCP_TARGET_ID",
    "ExternalMCPArtifactResolution",
    "ExternalMCPDescriptor",
    "ExternalMCPSelection",
    "ExternalMCPSelectionKind",
    "ExternalMCPTargetPreview",
    "ExternalMCPToolPolicy",
    "external_mcp_descriptor_to_dict",
    "external_mcp_selection_from_dict",
    "external_mcp_server_spec",
    "normalize_external_mcp_candidate",
    "project_external_mcp_target",
]
