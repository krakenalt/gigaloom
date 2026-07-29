"""FastAPI app for the minimal Unified Harness browser UI."""

from __future__ import annotations

from fastapi import FastAPI

from gpt2giga_harness.config import (
    HarnessConfig,
)
from gpt2giga_harness.environment_actions import (
    EnvironmentCommitService,
)
from gpt2giga_harness.environment_pull_requests import (
    EnvironmentPullRequestService,
)
from gpt2giga_harness.environment_push import (
    EnvironmentPushService,
)
from gpt2giga_harness.github_environments import GitHubEnvironmentService
from gpt2giga_harness.integration_flows import IntegrationFlowService
from gpt2giga_harness.integration_groups import GroupedIntegrationService
from gpt2giga_harness.integration_lifecycle import IntegrationLifecycleService
from gpt2giga_harness.native.process import (
    NativeProcessManager,
)
from gpt2giga_harness.native.registry import (
    NativeHistoryConnectorRegistry,
)
from gpt2giga_harness.native.store import (
    NativeSessionIndexStore,
)
from gpt2giga_harness.provider_authentication_broker import NativeLoginBroker
from gpt2giga_harness.provider_settings import ProviderSettingsService
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore
from gpt2giga_harness.sessions import (
    HarnessSessionStore,
)
from gpt2giga_harness.skill_library import SkillLibraryService
from gpt2giga_harness.ui.async_execution import (
    AsyncDiagnosticsMiddleware,
    ConformantAPIRoute,
    async_handler_contract_errors,
)
from gpt2giga_harness.ui.container import build_app_services
from gpt2giga_harness.ui.dependencies import install_app_services
from gpt2giga_harness.ui.execution_contracts import install_execution_contracts
from gpt2giga_harness.ui.mutation_contracts import install_mutation_contracts
from gpt2giga_harness.ui.remote_identity import (
    RemoteIdentityError,
    RemoteOIDCClient,
    RemoteOIDCSettings,
)
from gpt2giga_harness.ui.router_registry import install_application_routers
from gpt2giga_harness.ui.security import (
    HarnessUISecurityMiddleware,
    is_loopback_host,
)
from gpt2giga_harness.ui.services.lifecycle import create_app_lifespan


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
