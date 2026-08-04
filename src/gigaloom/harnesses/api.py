"""Public facade for native consumers of harness adapter utilities."""

from __future__ import annotations

from importlib import import_module
from typing import Any

attachment_raw_metadata: Any
build_safe_env: Any
claude_code_custom_headers: Any
cli_args_from_attachments: Any
gemini_cli_custom_headers: Any
prompt_with_attachments: Any
AgentIdentityInventory: Any
AgentProfileV1: Any
AcpCapabilitySnapshotV1: Any
AcpClient: Any
AcpPromptHandle: Any
AcpSessionBindingV1: Any
AcpSessionPageV1: Any
CapabilityCatalogFactState: Any
CapabilityCatalogFactV1: Any
CapabilityCatalogRouteV1: Any
CapabilityCatalogV1: Any
CapabilitySnapshotState: Any
RouteCapabilitySnapshotV1: Any
StructuredRouteDescriptorV1: Any
bind_structured_route_descriptors: Any
build_capability_catalog: Any
load_agent_profile_registry: Any
load_builtin_agent_profiles: Any
create_agent_runtime_service: Any
project_acp_capability_snapshot: Any
acp_harnesses: Any
begin_prompt: Any
list_sessions: Any
load_session: Any

__all__ = [
    "attachment_raw_metadata",
    "acp_harnesses",
    "AcpCapabilitySnapshotV1",
    "AcpClient",
    "AcpPromptHandle",
    "AcpSessionBindingV1",
    "AcpSessionPageV1",
    "AgentIdentityInventory",
    "AgentProfileV1",
    "build_safe_env",
    "bind_structured_route_descriptors",
    "build_capability_catalog",
    "begin_prompt",
    "CapabilityCatalogFactState",
    "CapabilityCatalogFactV1",
    "CapabilityCatalogRouteV1",
    "CapabilityCatalogV1",
    "CapabilitySnapshotState",
    "claude_code_custom_headers",
    "cli_args_from_attachments",
    "create_agent_runtime_service",
    "gemini_cli_custom_headers",
    "load_agent_profile_registry",
    "load_builtin_agent_profiles",
    "list_sessions",
    "load_session",
    "prompt_with_attachments",
    "project_acp_capability_snapshot",
    "RouteCapabilitySnapshotV1",
    "StructuredRouteDescriptorV1",
]

_LAZY_EXPORTS = {
    "acp_harnesses": (
        "gigaloom.harnesses.managed_acp",
        "acp_harnesses",
    ),
    "attachment_raw_metadata": (
        "gigaloom.harnesses.attachment_plan",
        "attachment_raw_metadata",
    ),
    "build_safe_env": (
        "gigaloom.harnesses.agent_cli",
        "build_safe_env",
    ),
    "claude_code_custom_headers": (
        "gigaloom.harnesses.claude_code",
        "claude_code_custom_headers",
    ),
    "cli_args_from_attachments": (
        "gigaloom.harnesses.attachment_plan",
        "cli_args_from_attachments",
    ),
    "gemini_cli_custom_headers": (
        "gigaloom.harnesses.gemini_cli",
        "gemini_cli_custom_headers",
    ),
    "prompt_with_attachments": (
        "gigaloom.harnesses.attachment_plan",
        "prompt_with_attachments",
    ),
    "AcpCapabilitySnapshotV1": (
        "gigaloom.harnesses.acp.api",
        "AcpCapabilitySnapshotV1",
    ),
    **{
        name: ("gigaloom.harnesses.acp.api", name)
        for name in (
            "AcpClient",
            "AcpPromptHandle",
            "AcpSessionBindingV1",
            "AcpSessionPageV1",
            "begin_prompt",
            "list_sessions",
            "load_session",
        )
    },
    "AgentProfileV1": (
        "gigaloom.harnesses.agent_profiles.api",
        "AgentProfileV1",
    ),
    **{
        name: ("gigaloom.harnesses.agent_profiles.installations", name)
        for name in (
            "AgentIdentityInventory",
            "create_agent_runtime_service",
        )
    },
    "load_agent_profile_registry": (
        "gigaloom.harnesses.agent_profiles.api",
        "load_agent_profile_registry",
    ),
    "load_builtin_agent_profiles": (
        "gigaloom.harnesses.agent_profiles.api",
        "load_builtin_agent_profiles",
    ),
    **{
        name: ("gigaloom.harnesses.capability_catalog", name)
        for name in (
            "CapabilityCatalogFactState",
            "CapabilityCatalogFactV1",
            "CapabilityCatalogRouteV1",
            "CapabilityCatalogV1",
            "CapabilitySnapshotState",
            "RouteCapabilitySnapshotV1",
            "StructuredRouteDescriptorV1",
            "bind_structured_route_descriptors",
            "build_capability_catalog",
            "project_acp_capability_snapshot",
        )
    },
}


def __getattr__(name: str) -> Any:
    """Resolve one compatibility-stable adapter utility lazily."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
