"""Typed construction and ownership of FastAPI application services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gpt2giga_harness.application import SessionApplicationService
from gpt2giga_harness.arena import FilesystemHarnessArenaStore
from gpt2giga_harness.attachments import FilesystemAttachmentStore
from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.environment_actions import (
    EnvironmentCommitError,
    EnvironmentCommitService,
    GovernedEnvironmentCommitService,
)
from gpt2giga_harness.environment_pull_requests import (
    EnvironmentPullRequestError,
    EnvironmentPullRequestService,
    GovernedEnvironmentPullRequestService,
)
from gpt2giga_harness.environment_push import (
    EnvironmentPushError,
    EnvironmentPushService,
    GovernedEnvironmentPushService,
)
from gpt2giga_harness.evals import FilesystemHarnessEvalStore
from gpt2giga_harness.github_environments import GitHubEnvironmentService
from gpt2giga_harness.handoff_capsules import HandoffCapsuleService
from gpt2giga_harness.integration_flows import IntegrationFlowService
from gpt2giga_harness.integration_groups import GroupedIntegrationService
from gpt2giga_harness.integration_lifecycle import IntegrationLifecycleService
from gpt2giga_harness.native.process import NativeProcessManager
from gpt2giga_harness.native.registry import (
    NativeHistoryConnectorRegistry,
    create_default_native_registry,
)
from gpt2giga_harness.native.store import (
    FilesystemNativeSessionIndexStore,
    NativeSessionIndexStore,
)
from gpt2giga_harness.project_memory import FilesystemProjectMemoryStore
from gpt2giga_harness.provider_authentication_broker import NativeLoginBroker
from gpt2giga_harness.provider_settings import ProviderSettingsService
from gpt2giga_harness.registry import HarnessRegistry, create_default_registry
from gpt2giga_harness.runtime.payloads import DurableJobPayloadStore
from gpt2giga_harness.runtime.policy import PolicyEngine
from gpt2giga_harness.runtime.reconcile import (
    RuntimeReconciler,
    RuntimeReconciliationReport,
)
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore
from gpt2giga_harness.runtime.worker import DurableJobDispatcher
from gpt2giga_harness.schedules import ScheduleService
from gpt2giga_harness.session_runner import HarnessSessionRunner
from gpt2giga_harness.sessions import (
    FilesystemHarnessSessionStore,
    HarnessSessionStore,
)
from gpt2giga_harness.sessions.event_stream import RunEventBroker
from gpt2giga_harness.settings import HarnessSettingsStore
from gpt2giga_harness.skill_library import SkillLibraryService
from gpt2giga_harness.trace_replay import TraceReplayService
from gpt2giga_harness.ui.async_execution import AsyncExecutionDiagnostics
from gpt2giga_harness.ui.remote_identity import RemoteOIDCClient
from gpt2giga_harness.ui.security import HarnessUISecurity
from gpt2giga_harness.ui.services import ActiveHeadlessRun
from gpt2giga_harness.workbench_protocol import WorkbenchBackbone
from gpt2giga_harness.workbench_resources import (
    WorkbenchPreferenceStore,
    WorkbenchResourceService,
)


@dataclass(frozen=True, slots=True)
class AppServices:
    """All application-scoped dependencies owned by one FastAPI app."""

    config: HarnessConfig
    ui_security: HarnessUISecurity
    registry: HarnessRegistry
    session_store: HarnessSessionStore
    runtime_store: RuntimeCoordinationStore | None
    runtime_reconciliation: RuntimeReconciliationReport | None
    session_runner: HarnessSessionRunner
    session_service: SessionApplicationService
    job_dispatcher: DurableJobDispatcher | None
    trace_replay_service: TraceReplayService
    handoff_capsule_service: HandoffCapsuleService
    policy_engine: PolicyEngine
    attachment_store: FilesystemAttachmentStore
    arena_store: FilesystemHarnessArenaStore
    eval_store: FilesystemHarnessEvalStore
    schedule_service: ScheduleService | None
    project_memory_store: FilesystemProjectMemoryStore
    native_registry: NativeHistoryConnectorRegistry
    native_index_store: NativeSessionIndexStore
    native_process_manager: NativeProcessManager
    async_diagnostics: AsyncExecutionDiagnostics
    run_event_broker: RunEventBroker
    workbench_backbone: WorkbenchBackbone
    workbench_resources: WorkbenchResourceService
    settings_store: HarnessSettingsStore
    provider_settings_service: ProviderSettingsService
    native_login_broker: NativeLoginBroker
    integration_flow_service: IntegrationFlowService
    grouped_integration_service: GroupedIntegrationService
    integration_lifecycle_service: IntegrationLifecycleService
    skill_library_service: SkillLibraryService
    github_environment_service: GitHubEnvironmentService
    environment_commit_service: EnvironmentCommitService | None
    governed_environment_commit_service: GovernedEnvironmentCommitService | None
    environment_push_service: EnvironmentPushService | None
    governed_environment_push_service: GovernedEnvironmentPushService | None
    environment_pull_request_service: EnvironmentPullRequestService | None
    governed_environment_pull_request_service: (
        GovernedEnvironmentPullRequestService | None
    )
    active_headless_runs: dict[str, ActiveHeadlessRun] = field(default_factory=dict)
    session_navigation_mutations: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )

    def legacy_state(self) -> dict[str, object | None]:
        """Return compatibility bindings for existing routers and integrations."""
        return {
            "harness_config": self.config,
            "harness_ui_security": self.ui_security,
            "harness_registry": self.registry,
            "harness_session_store": self.session_store,
            "harness_runtime_store": self.runtime_store,
            "harness_runtime_reconciliation": self.runtime_reconciliation,
            "harness_session_runner": self.session_runner,
            "harness_session_service": self.session_service,
            "harness_job_dispatcher": self.job_dispatcher,
            "harness_trace_replay_service": self.trace_replay_service,
            "harness_handoff_capsule_service": self.handoff_capsule_service,
            "harness_policy_engine": self.policy_engine,
            "harness_attachment_store": self.attachment_store,
            "harness_arena_store": self.arena_store,
            "harness_eval_store": self.eval_store,
            "harness_schedule_service": self.schedule_service,
            "harness_project_memory_store": self.project_memory_store,
            "harness_native_registry": self.native_registry,
            "harness_native_index_store": self.native_index_store,
            "harness_native_process_manager": self.native_process_manager,
            "harness_async_diagnostics": self.async_diagnostics,
            "harness_run_event_broker": self.run_event_broker,
            "harness_workbench_backbone": self.workbench_backbone,
            "harness_workbench_resources": self.workbench_resources,
            "harness_settings_store": self.settings_store,
            "harness_provider_settings_service": self.provider_settings_service,
            "harness_native_login_broker": self.native_login_broker,
            "harness_integration_flow_service": self.integration_flow_service,
            "harness_grouped_integration_service": self.grouped_integration_service,
            "harness_integration_lifecycle_service": (
                self.integration_lifecycle_service
            ),
            "harness_skill_library_service": self.skill_library_service,
            "harness_github_environment_service": self.github_environment_service,
            "harness_environment_commit_service": self.environment_commit_service,
            "harness_governed_environment_commit_service": (
                self.governed_environment_commit_service
            ),
            "harness_environment_push_service": self.environment_push_service,
            "harness_governed_environment_push_service": (
                self.governed_environment_push_service
            ),
            "harness_environment_pull_request_service": (
                self.environment_pull_request_service
            ),
            "harness_governed_environment_pull_request_service": (
                self.governed_environment_pull_request_service
            ),
        }


def build_app_services(
    *,
    config: HarnessConfig,
    registry: HarnessRegistry | None = None,
    session_store: HarnessSessionStore | None = None,
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
) -> AppServices:
    """Construct the application service graph without creating the ASGI app."""
    registry = registry or create_default_registry()
    session_store = session_store or FilesystemHarnessSessionStore(config.data_dir)
    if runtime_store is None and isinstance(
        session_store, FilesystemHarnessSessionStore
    ):
        runtime_store = RuntimeCoordinationStore(session_store.data_dir)
    runtime_reconciliation = (
        RuntimeReconciler(runtime_store, session_store).reconcile()
        if runtime_store is not None
        else None
    )
    native_registry = native_registry or create_default_native_registry(
        data_dir=config.data_dir
    )
    native_index_store = native_index_store or FilesystemNativeSessionIndexStore(
        config.data_dir
    )
    native_process_manager = native_process_manager or NativeProcessManager(
        session_store=session_store,
        runtime_store=runtime_store,
    )
    attachment_store = FilesystemAttachmentStore(config.data_dir)
    arena_store = FilesystemHarnessArenaStore(config.data_dir)
    eval_store = FilesystemHarnessEvalStore(config.data_dir)
    memory_store = FilesystemProjectMemoryStore()
    settings_store = HarnessSettingsStore(config.data_dir, config)
    provider_settings_service = provider_settings_service or ProviderSettingsService(
        config.data_dir
    )
    native_login_broker = native_login_broker or NativeLoginBroker(
        config.data_dir,
        resolution_provider=lambda provider_id: registry.get(
            provider_id
        ).executable_resolution(),
        capability_provider=lambda provider_id: registry.get(
            provider_id
        ).capability_probe(),
    )
    integration_flow_service = integration_flow_service or IntegrationFlowService(
        config.data_dir
    )
    skill_library_service = skill_library_service or SkillLibraryService(
        config.data_dir
    )
    github_environment_service = (
        github_environment_service or GitHubEnvironmentService()
    )
    environment_commit_service = _environment_commit_service(
        config, environment_commit_service
    )
    environment_push_service = _environment_push_service(
        config, environment_push_service
    )
    environment_pull_request_service = _environment_pull_request_service(
        config, environment_pull_request_service
    )
    grouped_integration_service = (
        grouped_integration_service
        or GroupedIntegrationService(
            config.data_dir,
            flow_service=integration_flow_service,
        )
    )
    integration_lifecycle_service = (
        integration_lifecycle_service
        or IntegrationLifecycleService(
            config.data_dir,
            flow_service=integration_flow_service,
            group_service=grouped_integration_service,
        )
    )
    runner = HarnessSessionRunner(
        registry=registry,
        config=config,
        store=session_store,
        attachment_store=attachment_store,
        memory_store=memory_store,
        provider_account_provider=native_login_broker,
    )
    dispatcher = (
        DurableJobDispatcher(
            runtime_store=runtime_store,
            payload_store=DurableJobPayloadStore(config.data_dir),
            runner=runner,
        )
        if runtime_store is not None
        and isinstance(session_store, FilesystemHarnessSessionStore)
        else None
    )
    session_service = SessionApplicationService(
        runner=runner,
        settings_store=settings_store,
        runtime_store=runtime_store,
        dispatcher=dispatcher,
    )
    policy_engine = PolicyEngine(runtime_store)
    async_diagnostics = AsyncExecutionDiagnostics()
    run_event_broker = getattr(session_store, "event_broker", RunEventBroker())
    workbench_backbone = WorkbenchBackbone()
    workbench_resources = WorkbenchResourceService(
        session_store=session_store,
        runtime_store=runtime_store,
        preference_store=WorkbenchPreferenceStore(config.data_dir),
        integration_service=integration_flow_service,
    )
    return AppServices(
        config=config,
        ui_security=HarnessUISecurity(config, oidc_client=remote_oidc_client),
        registry=registry,
        session_store=session_store,
        runtime_store=runtime_store,
        runtime_reconciliation=runtime_reconciliation,
        session_runner=runner,
        session_service=session_service,
        job_dispatcher=dispatcher,
        trace_replay_service=TraceReplayService(runner, dispatcher=dispatcher),
        handoff_capsule_service=HandoffCapsuleService(
            store=session_store,
            registry=registry,
            runtime_store=runtime_store,
        ),
        policy_engine=policy_engine,
        attachment_store=attachment_store,
        arena_store=arena_store,
        eval_store=eval_store,
        schedule_service=(
            ScheduleService(
                runtime_store=runtime_store,
                runner=runner,
                dispatcher=dispatcher,
                eval_store=eval_store,
            )
            if runtime_store is not None and dispatcher is not None
            else None
        ),
        project_memory_store=memory_store,
        native_registry=native_registry,
        native_index_store=native_index_store,
        native_process_manager=native_process_manager,
        async_diagnostics=async_diagnostics,
        run_event_broker=run_event_broker,
        workbench_backbone=workbench_backbone,
        workbench_resources=workbench_resources,
        settings_store=settings_store,
        provider_settings_service=provider_settings_service,
        native_login_broker=native_login_broker,
        integration_flow_service=integration_flow_service,
        grouped_integration_service=grouped_integration_service,
        integration_lifecycle_service=integration_lifecycle_service,
        skill_library_service=skill_library_service,
        github_environment_service=github_environment_service,
        environment_commit_service=environment_commit_service,
        governed_environment_commit_service=(
            GovernedEnvironmentCommitService(
                environment_commit_service,
                runtime_store,
                policy_engine,
            )
            if runtime_store is not None and environment_commit_service is not None
            else None
        ),
        environment_push_service=environment_push_service,
        governed_environment_push_service=(
            GovernedEnvironmentPushService(
                environment_push_service,
                runtime_store,
                policy_engine,
            )
            if runtime_store is not None and environment_push_service is not None
            else None
        ),
        environment_pull_request_service=environment_pull_request_service,
        governed_environment_pull_request_service=(
            GovernedEnvironmentPullRequestService(
                environment_pull_request_service,
                runtime_store,
                policy_engine,
            )
            if runtime_store is not None
            and environment_pull_request_service is not None
            else None
        ),
    )


def _environment_commit_service(
    config: HarnessConfig,
    service: EnvironmentCommitService | None,
) -> EnvironmentCommitService | None:
    if service is not None:
        return service
    try:
        return EnvironmentCommitService(config.data_dir)
    except EnvironmentCommitError:
        return None


def _environment_push_service(
    config: HarnessConfig,
    service: EnvironmentPushService | None,
) -> EnvironmentPushService | None:
    if service is not None:
        return service
    try:
        return EnvironmentPushService(config.data_dir)
    except EnvironmentPushError:
        return None


def _environment_pull_request_service(
    config: HarnessConfig,
    service: EnvironmentPullRequestService | None,
) -> EnvironmentPullRequestService | None:
    if service is not None:
        return service
    try:
        return EnvironmentPullRequestService(config.data_dir)
    except EnvironmentPullRequestError:
        return None
