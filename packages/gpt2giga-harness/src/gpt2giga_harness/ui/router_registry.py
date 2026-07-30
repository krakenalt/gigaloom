"""Ordered installation of all FastAPI domain routers."""

from __future__ import annotations

from fastapi import FastAPI

from gpt2giga_harness.provider_authentication_broker import NativeLoginBroker
from gpt2giga_harness.ui.container import AppServices
from gpt2giga_harness.ui.routers.agents import router as agents_router
from gpt2giga_harness.ui.routers.approvals import router as approvals_router
from gpt2giga_harness.ui.routers.arena import create_router as create_arena_router
from gpt2giga_harness.ui.routers.attachments import (
    create_router as create_attachments_router,
)
from gpt2giga_harness.ui.routers.automation import router as automation_router
from gpt2giga_harness.ui.routers.catalog import create_router as create_catalog_router
from gpt2giga_harness.ui.routers.cockpit import router as cockpit_router
from gpt2giga_harness.ui.routers.compatibility import router as compatibility_router
from gpt2giga_harness.ui.routers.editor import create_router as create_editor_router
from gpt2giga_harness.ui.routers.environments import router as environments_router
from gpt2giga_harness.ui.routers.evals import create_router as create_evals_router
from gpt2giga_harness.ui.routers.evaluate import router as evaluate_router
from gpt2giga_harness.ui.routers.files import create_file_preview_router
from gpt2giga_harness.ui.routers.handoff_capsules import (
    router as handoff_capsules_router,
)
from gpt2giga_harness.ui.routers.integrations import router as integrations_router
from gpt2giga_harness.ui.routers.legacy_run import (
    create_router as create_legacy_run_router,
)
from gpt2giga_harness.ui.routers.native_process_start import (
    create_router as create_native_process_start_router,
)
from gpt2giga_harness.ui.routers.native_processes import (
    create_router as create_native_processes_router,
)
from gpt2giga_harness.ui.routers.native_sessions import (
    create_router as create_native_sessions_router,
)
from gpt2giga_harness.ui.routers.project_memory import (
    create_router as create_project_memory_router,
)
from gpt2giga_harness.ui.routers.project_tools import (
    create_router as create_project_tools_router,
)
from gpt2giga_harness.ui.routers.projects import create_router as create_projects_router
from gpt2giga_harness.ui.routers.provider_handoffs import create_provider_handoff_router
from gpt2giga_harness.ui.routers.run_history import (
    create_router as create_run_history_router,
)
from gpt2giga_harness.ui.routers.run_streams import (
    create_router as create_run_streams_router,
)
from gpt2giga_harness.ui.routers.run_worktrees import (
    create_router as create_run_worktrees_router,
)
from gpt2giga_harness.ui.routers.runs import router as runs_router
from gpt2giga_harness.ui.routers.schedules import router as schedules_router
from gpt2giga_harness.ui.routers.session_catalog import (
    create_router as create_session_catalog_router,
)
from gpt2giga_harness.ui.routers.session_runs import (
    create_router as create_session_runs_router,
)
from gpt2giga_harness.ui.routers.settings import router as settings_router
from gpt2giga_harness.ui.routers.shell import create_shell_router
from gpt2giga_harness.ui.routers.tools import router as tools_router
from gpt2giga_harness.ui.routers.trace_replays import router as trace_replays_router
from gpt2giga_harness.ui.routers.tui_actions import router as tui_actions_router
from gpt2giga_harness.ui.routers.workbench_resources import (
    router as workbench_resources_router,
)
from gpt2giga_harness.ui.routers.workbench_state import router as workbench_state_router
from gpt2giga_harness.ui.routers.workflows import router as workflows_router


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
    app.include_router(automation_router)
    app.include_router(approvals_router)
    app.include_router(cockpit_router)
    app.include_router(compatibility_router)
    app.include_router(evaluate_router)
    app.include_router(environments_router)
    app.include_router(integrations_router)
    app.include_router(tools_router)
    app.include_router(tui_actions_router)
    app.include_router(workbench_state_router)
    app.include_router(workbench_resources_router)
    app.include_router(workflows_router)
    app.include_router(runs_router)
    app.include_router(trace_replays_router)
    app.include_router(handoff_capsules_router)
    app.include_router(schedules_router)
    app.include_router(settings_router)
    app.include_router(create_file_preview_router(services.config.data_dir))
    app.include_router(create_provider_handoff_router(services.registry))
    # The shell catch-all must remain last so unknown API and asset paths never
    # become HTML responses.
    app.include_router(create_shell_router(services.ui_security))
