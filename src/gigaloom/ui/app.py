"""FastAPI app for the minimal Unified Harness browser UI."""

from __future__ import annotations

from fastapi import FastAPI

from gigaloom.config import (
    HarnessConfig,
)
from gigaloom.environment_actions import (
    EnvironmentCommitService,
)
from gigaloom.environment_pull_requests import (
    EnvironmentPullRequestService,
)
from gigaloom.environment_push import (
    EnvironmentPushService,
)
from gigaloom.github_environments import GitHubEnvironmentService
from gigaloom.integration_flows import IntegrationFlowService
from gigaloom.integration_groups import GroupedIntegrationService
from gigaloom.integration_lifecycle import IntegrationLifecycleService
from gigaloom.native.process import (
    NativeProcessManager,
)
from gigaloom.native.registry import (
    NativeHistoryConnectorRegistry,
)
from gigaloom.native.store import (
    NativeSessionIndexStore,
)
from gigaloom.provider_authentication_broker import NativeLoginBroker
from gigaloom.provider_settings import ProviderSettingsService
from gigaloom.registry import HarnessRegistry
from gigaloom.runtime.action_inbox.api import ActionInboxService
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.sessions import (
    HarnessSessionStore,
)
from gigaloom.skill_library import SkillLibraryService
from gigaloom.ui.async_execution import (
    AsyncDiagnosticsMiddleware,
    ConformantAPIRoute,
    async_handler_contract_errors,
)
from gigaloom.ui.container import build_app_services
from gigaloom.ui.dependencies import install_app_services
from gigaloom.ui.execution_contracts import install_execution_contracts
from gigaloom.ui.mutation_contracts import install_mutation_contracts
from gigaloom.ui.remote_identity import (
    RemoteIdentityError,
    RemoteOIDCClient,
    RemoteOIDCSettings,
)
from gigaloom.ui.router_registry import install_application_routers
from gigaloom.ui.security import (
    HarnessUISecurityMiddleware,
    is_loopback_host,
)
from gigaloom.ui.services.context_impact import ContextProjectionQuery
from gigaloom.ui.services.lifecycle import create_app_lifespan
from gigaloom.ui.services.operator_workspace import OperatorEvidenceQuery
from gigaloom.ui.streaming.operator_events import OperatorEventBroker


def create_app(
    config: HarnessConfig | None = None,
    registry: HarnessRegistry | None = None,
    store: HarnessSessionStore | None = None,
    native_registry: NativeHistoryConnectorRegistry | None = None,
    native_index_store: NativeSessionIndexStore | None = None,
    native_process_manager: NativeProcessManager | None = None,
    runtime_store: RuntimeCoordinationStore | None = None,
    provider_settings_service: ProviderSettingsService | None = None,
    native_login_broker: NativeLoginBroker | None = None,
    integration_flow_service: IntegrationFlowService | None = None,
    grouped_integration_service: GroupedIntegrationService | None = None,
    integration_lifecycle_service: IntegrationLifecycleService | None = None,
    skill_library_service: SkillLibraryService | None = None,
    github_environment_service: GitHubEnvironmentService | None = None,
    environment_commit_service: EnvironmentCommitService | None = None,
    environment_push_service: EnvironmentPushService | None = None,
    environment_pull_request_service: EnvironmentPullRequestService | None = None,
    remote_oidc_client: RemoteOIDCClient | None = None,
    context_projection_query: ContextProjectionQuery | None = None,
    operator_evidence_query: OperatorEvidenceQuery | None = None,
    action_inbox_service: ActionInboxService | None = None,
    operator_event_broker: OperatorEventBroker | None = None,
) -> FastAPI:
    """Create the Unified Harness UI app."""
    config = config or HarnessConfig.from_env()
    validate_ui_bind(config, allow_remote=None)
    services = build_app_services(
        config=config,
        registry=registry,
        session_store=store,
        native_registry=native_registry,
        native_index_store=native_index_store,
        native_process_manager=native_process_manager,
        runtime_store=runtime_store,
        provider_settings_service=provider_settings_service,
        native_login_broker=native_login_broker,
        integration_flow_service=integration_flow_service,
        grouped_integration_service=grouped_integration_service,
        integration_lifecycle_service=integration_lifecycle_service,
        skill_library_service=skill_library_service,
        github_environment_service=github_environment_service,
        environment_commit_service=environment_commit_service,
        environment_push_service=environment_push_service,
        environment_pull_request_service=environment_pull_request_service,
        remote_oidc_client=remote_oidc_client,
        context_projection_query=context_projection_query,
        operator_evidence_query=operator_evidence_query,
        action_inbox_service=action_inbox_service,
        operator_event_broker=operator_event_broker,
    )
    async_diagnostics = services.async_diagnostics

    app = FastAPI(
        title="gpt2giga Unified Harness",
        docs_url=None,
        redoc_url=None,
        lifespan=create_app_lifespan(services),
    )
    app.router.route_class = ConformantAPIRoute
    app.add_middleware(
        HarnessUISecurityMiddleware,
        security=services.ui_security,
    )
    app.add_middleware(
        AsyncDiagnosticsMiddleware,
        diagnostics=async_diagnostics,
    )
    install_app_services(app, services)

    install_application_routers(
        app,
        services,
        native_login_broker=native_login_broker,
    )
    install_mutation_contracts(app)
    install_execution_contracts(app)
    if handler_errors := async_handler_contract_errors(app.routes):
        raise RuntimeError(
            "Harness async handler contract invalid: " + "; ".join(handler_errors)
        )
    return app


def validate_ui_bind(
    config: HarnessConfig,
    *,
    allow_remote: bool | None,
) -> None:
    """Admit remote binding only for the complete accepted OIDC profile."""
    if is_loopback_host(config.ui_host):
        return
    if allow_remote is False:
        raise ValueError(
            f"Refusing to bind GigaLoom UI to non-loopback host {config.ui_host}. "
            "Pass --allow-remote only with the complete single-issuer OIDC profile."
        )
    try:
        RemoteOIDCSettings.from_config(config)
    except RemoteIdentityError as exc:
        raise ValueError(
            f"Refusing to bind GigaLoom UI to non-loopback host {config.ui_host}. {exc}"
        ) from exc
