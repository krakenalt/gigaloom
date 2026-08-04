"""Lazy command-handler registry."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any


CommandHandler = Callable[[Any, Any], int]

_PROVIDER_HANDLERS = frozenset(
    {
        "_handle_provider_add",
        "_handle_provider_discover",
        "_handle_provider_edit",
        "_handle_provider_list",
        "_handle_provider_migrate",
        "_handle_provider_show",
        "_handle_provider_test",
    }
)
_HARNESS_HANDLERS = frozenset({"_handle_harness_list"})
_AGENT_PROFILE_HANDLERS = frozenset(
    {
        "_handle_agent_probe_plan",
        "_handle_agent_profile_add",
        "_handle_agent_profile_discover",
        "_handle_agent_profile_inspect",
        "_handle_agent_profile_list",
        "_handle_agent_profile_remove",
    }
)
_AGENT_RUNTIME_HANDLERS = frozenset(
    {
        "_handle_agent_runtime_add",
        "_handle_agent_runtime_activate",
        "_handle_agent_runtime_inspect",
        "_handle_agent_runtime_list",
        "_handle_agent_runtime_lock",
        "_handle_agent_runtime_outdated",
        "_handle_agent_runtime_probe",
        "_handle_agent_runtime_remove",
        "_handle_agent_runtime_rollback",
        "_handle_agent_runtime_search",
        "_handle_agent_runtime_sync",
        "_handle_agent_runtime_update",
    }
)
_HEADLESS_HANDLERS = frozenset(
    {
        "_handle_headless_contract",
        "_handle_headless_doctor",
        "_handle_headless_env",
    }
)
_UI_HANDLERS = frozenset(
    {
        "_handle_ui",
        "_handle_ui_identity_revoke_all",
        "_handle_ui_identity_validate",
    }
)
_WORKER_HANDLERS = frozenset(
    {
        "_handle_worker_start",
        "_handle_worker_status",
        "_handle_worker_stop_on_idle",
    }
)
_STATE_HANDLERS = frozenset(
    {
        "_handle_state_backup",
        "_handle_state_migrate",
        "_handle_state_restore",
        "_handle_state_rollback",
        "_handle_state_upgrade",
        "_handle_state_verify",
    }
)
_PROJECT_CATALOG_HANDLERS = frozenset(
    {
        "_handle_project_catalog_add",
        "_handle_project_catalog_list",
        "_handle_project_catalog_move_session",
        "_handle_project_catalog_relocate",
        "_handle_project_catalog_remove",
        "_handle_project_catalog_rename",
        "_handle_project_profile_create",
        "_handle_project_profile_delete",
        "_handle_project_profile_list",
        "_handle_project_profile_update",
    }
)
_ROUTE_ADVISOR_HANDLERS = frozenset(
    {
        "_handle_route_override",
        "_handle_route_recommend",
        "_handle_route_show",
    }
)
_CAPSULE_HANDLERS = frozenset({"_handle_capsule_export", "_handle_capsule_verify"})
_RELIABILITY_HANDLERS = frozenset(
    {"_handle_reliability_check", "_handle_reliability_simulate"}
)
_UPGRADE_RADAR_HANDLERS = frozenset({"_handle_agent_upgrade_check"})
_VISUAL_EVAL_HANDLERS = frozenset({"_handle_eval_visual"})
_THREAD_RELAY_HANDLERS = frozenset(
    {
        "_handle_thread_list",
        "_handle_thread_read",
        "_handle_thread_send",
        "_handle_thread_status",
    }
)
_GATEWAY_HANDLERS = frozenset(
    {
        "_handle_gateway_list",
        "_handle_gateway_inspect",
        "_handle_gateway_doctor",
        "_handle_gateway_start",
        "_handle_gateway_stop",
    }
)
_SCHEMA_HANDLERS = frozenset({"_handle_schema_list", "_handle_schema_export"})
_PRODUCT_EVIDENCE_HANDLERS = frozenset({"_handle_product_beta_evidence"})


def resolve_handler(name: str) -> CommandHandler:
    """Import and return one registered command handler."""
    if name in _AGENT_PROFILE_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.agent_profiles"
    elif name in _AGENT_RUNTIME_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.agent_runtimes"
    elif name in _HEADLESS_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.headless"
    elif name in _PROVIDER_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.provider"
    elif name in _HARNESS_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.harness"
    elif name in _UI_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.ui"
    elif name in _WORKER_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.worker"
    elif name in _STATE_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.state"
    elif name in _PROJECT_CATALOG_HANDLERS:
        module = import_module("gigaloom.cli_commands.handlers.projects")
        return module.resolve_project_command_handler(name)
    elif name == "_handle_project_launch":
        module_name = "gigaloom.cli_commands.handlers.project_launch"
    elif name in _ROUTE_ADVISOR_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.route_advisor"
    elif name in _CAPSULE_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.capsules"
    elif name in _RELIABILITY_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.reliability"
    elif name in _UPGRADE_RADAR_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.upgrade_radar"
    elif name in _VISUAL_EVAL_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.visual_eval"
    elif name in _THREAD_RELAY_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.thread_relay"
    elif name in _GATEWAY_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.gateway"
    elif name in _SCHEMA_HANDLERS:
        module_name = "gigaloom.automation.schemas.cli"
    elif name in _PRODUCT_EVIDENCE_HANDLERS:
        module_name = "gigaloom.cli_commands.handlers.product_evidence"
    elif name == "_handle_run_command":
        module_name = "gigaloom.cli_commands.handlers.runs"
    else:
        module_name = "gigaloom.cli"
    handler = getattr(import_module(module_name), name)
    return handler
