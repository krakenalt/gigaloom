"""Ordered installation of all FastAPI domain routers."""

from __future__ import annotations

from fastapi import FastAPI

from gigaloom.provider_authentication_broker import NativeLoginBroker
from gigaloom.ui.container import AppServices
from gigaloom.ui.routers.agents import router as agents_router
from gigaloom.ui.routers.agent_installations import (
    create_router as create_agent_installations_router,
)
from gigaloom.ui.routers.agent_registry import (
    create_router as create_agent_registry_router,
)
from gigaloom.ui.routers.approvals import router as approvals_router
from gigaloom.ui.routers.arena import create_router as create_arena_router
from gigaloom.ui.routers.attachments import (
    create_router as create_attachments_router,
)
from gigaloom.ui.routers.automation import router as automation_router
from gigaloom.ui.routers.catalog import create_router as create_catalog_router
from gigaloom.ui.routers.cockpit import router as cockpit_router
from gigaloom.ui.routers.compatibility import router as compatibility_router
from gigaloom.ui.routers.context_impact import (
    create_router as create_context_impact_router,
)
from gigaloom.ui.routers.credentials import (
    create_router as create_credentials_router,
)
from gigaloom.ui.routers.editor import create_router as create_editor_router
from gigaloom.ui.routers.environments import router as environments_router
from gigaloom.ui.routers.evals import create_router as create_evals_router
from gigaloom.ui.routers.evaluate import router as evaluate_router
from gigaloom.ui.routers.files import create_file_preview_router
from gigaloom.ui.routers.handoff_capsules import (
    router as handoff_capsules_router,
)
from gigaloom.ui.routers.integrations import router as integrations_router
from gigaloom.ui.routers.legacy_run import (
    create_router as create_legacy_run_router,
)
from gigaloom.ui.routers.native_process_start import (
    create_router as create_native_process_start_router,
)
from gigaloom.ui.routers.native_processes import (
    create_router as create_native_processes_router,
)
from gigaloom.ui.routers.native_sessions import (
    create_router as create_native_sessions_router,
)
from gigaloom.ui.routers.operator_workspace import (
    create_router as create_operator_workspace_router,
)
from gigaloom.ui.routers.operator_arena import (
    create_router as create_operator_arena_router,
)
from gigaloom.ui.routers.operator_terminal import (
    create_router as create_operator_terminal_router,
)
from gigaloom.ui.routers.project_memory import (
    create_router as create_project_memory_router,
)
from gigaloom.ui.routers.project_catalog import (
    create_router as create_project_catalog_router,
)
from gigaloom.ui.routers.project_tools import (
    create_router as create_project_tools_router,
)
from gigaloom.ui.routers.projects import create_router as create_projects_router
from gigaloom.ui.routers.provider_handoffs import create_provider_handoff_router
from gigaloom.ui.routers.run_history import (
    create_router as create_run_history_router,
)
from gigaloom.ui.routers.route_advisor import (
    create_router as create_route_advisor_router,
)
from gigaloom.ui.routers.run_capsules import (
    create_router as create_run_capsules_router,
)
from gigaloom.ui.routers.mcp_apps import create_router as create_mcp_apps_router
from gigaloom.ui.routers.run_streams import (
    create_router as create_run_streams_router,
)
from gigaloom.ui.routers.run_worktrees import (
    create_router as create_run_worktrees_router,
)
from gigaloom.ui.routers.runs import router as runs_router
from gigaloom.ui.routers.schedules import router as schedules_router
from gigaloom.ui.routers.session_catalog import (
    create_router as create_session_catalog_router,
)
from gigaloom.ui.routers.session_runs import (
    create_router as create_session_runs_router,
)
from gigaloom.ui.routers.settings import router as settings_router
from gigaloom.ui.routers.shell import create_shell_router
from gigaloom.ui.routers.tools import router as tools_router
from gigaloom.ui.routers.trace_replays import router as trace_replays_router
from gigaloom.ui.routers.run_actions import router as run_actions_router
from gigaloom.ui.routers.workbench_resources import (
    router as workbench_resources_router,
)
from gigaloom.ui.routers.workbench_state import router as workbench_state_router
from gigaloom.ui.routers.workflows import router as workflows_router


def install_application_routers(
    app: FastAPI,
    services: AppServices,
    *,
    native_login_broker: NativeLoginBroker | None,
) -> None:
    """Install domain routers in deterministic compatibility order."""
    app.include_router(create_session_runs_router(services))
    app.include_router(create_run_streams_router(services))
    app.include_router(create_run_history_router(services))
    app.include_router(create_run_worktrees_router(services))
    app.include_router(create_legacy_run_router(services))
    app.include_router(create_catalog_router(services))
    app.include_router(create_projects_router(services))
    app.include_router(create_context_impact_router(services))
    app.include_router(
        create_credentials_router(services.operational_backends.credential_operator)
    )
    app.include_router(create_editor_router(services))
    app.include_router(create_project_memory_router(services))
    app.include_router(create_project_tools_router(services))
    app.include_router(create_evals_router(services))
    app.include_router(create_session_catalog_router(services))
    app.include_router(create_attachments_router(services))
    app.include_router(create_arena_router(services))
    app.include_router(create_native_sessions_router(services))
    app.include_router(
        create_native_process_start_router(
            services, native_login_broker=native_login_broker
        )
    )
    app.include_router(create_native_processes_router(services))
    app.include_router(agents_router)
    app.include_router(create_agent_registry_router(services.agent_runtimes.registry))
    app.include_router(
        create_agent_installations_router(services.agent_runtimes.installations)
    )
    app.include_router(automation_router)
    app.include_router(approvals_router)
    app.include_router(cockpit_router)
    app.include_router(compatibility_router)
    app.include_router(evaluate_router)
    app.include_router(environments_router)
    app.include_router(integrations_router)
    app.include_router(tools_router)
    app.include_router(run_actions_router)
    app.include_router(workbench_state_router)
    app.include_router(workbench_resources_router)
    app.include_router(workflows_router)
    app.include_router(runs_router)
    app.include_router(trace_replays_router)
    app.include_router(handoff_capsules_router)
    app.include_router(schedules_router)
    app.include_router(settings_router)
    app.include_router(create_operator_arena_router(services))
    app.include_router(create_operator_terminal_router(services))
    app.include_router(create_operator_workspace_router(services))
    app.include_router(create_file_preview_router(services.config.data_dir))
    app.include_router(create_provider_handoff_router(services.registry))
    app.include_router(create_project_catalog_router(services.project_catalog_service))
    app.include_router(create_route_advisor_router(services.route_advisor_service))
    app.include_router(create_mcp_apps_router(services.mcp_app_host_service))
    app.include_router(create_run_capsules_router(services.run_capsule_evidence_query))
    # The shell catch-all must remain last so unknown API and asset paths never
    # become HTML responses.
    app.include_router(create_shell_router(services.ui_security))
