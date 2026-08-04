"""Typed construction and ownership of FastAPI application services."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gigaloom.application import SessionApplicationService
from gigaloom.automation.api import (
    FilesystemVisualArtifactStore,
    FilesystemVisualGateStore,
)
from gigaloom.arena import FilesystemHarnessArenaStore
from gigaloom.attachments import FilesystemAttachmentStore
from gigaloom.config import HarnessConfig
from gigaloom.diagnostics.api import RecoveryReceiptService
from gigaloom.environment_actions import (
    EnvironmentCommitService,
    GovernedEnvironmentCommitService,
)
from gigaloom.environment_pull_requests import (
    EnvironmentPullRequestService,
    GovernedEnvironmentPullRequestService,
)
from gigaloom.environment_push import (
    EnvironmentPushService,
    GovernedEnvironmentPushService,
)
from gigaloom.evals import FilesystemHarnessEvalStore
from gigaloom.execution.api import (
    LocalRouteRecommendationSource,
    RouteAdvisorApplicationService,
    RouteRecommendationSource,
)
from gigaloom.harnesses.api import AgentProfileV1, acp_harnesses
from gigaloom.github_environments import GitHubEnvironmentService
from gigaloom.handoff_capsules import HandoffCapsuleService
from gigaloom.integration_flows import IntegrationFlowService
from gigaloom.integration_groups import GroupedIntegrationService
from gigaloom.integration_lifecycle import IntegrationLifecycleService
from gigaloom.native.process import NativeProcessManager
from gigaloom.native.registry import (
    NativeHistoryConnectorRegistry,
    create_default_native_registry,
)
from gigaloom.native.store import (
    FilesystemNativeSessionIndexStore,
    NativeSessionIndexStore,
)
from gigaloom.project_memory import FilesystemProjectMemoryStore
from gigaloom.projects.api import LaunchResolutionContextV1
from gigaloom.provider_authentication_broker import NativeLoginBroker
from gigaloom.provider_settings import ProviderSettingsService
from gigaloom.registry import HarnessRegistry, create_default_registry
from gigaloom.runtime.api import InMemoryCredentialBroker
from gigaloom.runtime.payloads import DurableJobPayloadStore
from gigaloom.runtime.policy import PolicyEngine
from gigaloom.runtime.action_inbox.api import ActionInboxService
from gigaloom.runtime.reconcile import (
    RuntimeReconciler,
    RuntimeReconciliationReport,
)
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.runtime.worker import DurableJobDispatcher
from gigaloom.review.api import (
    FilesystemLaneDeltaPacketStore,
    LaneDeltaBuilder,
    LaneDeltaLifecycleService,
    RouteDecisionRepository,
)
from gigaloom.review.capsules import (
    CapsuleSigner,
    FilesystemRunCapsuleRepository,
    RunCapsuleCapturePortsV1,
    RunCapsuleLifecycleService,
)
from gigaloom.schedules import ScheduleService
from gigaloom.session_runner import HarnessSessionRunner
from gigaloom.sessions import (
    FilesystemHarnessSessionStore,
    HarnessSessionStore,
)
from gigaloom.sessions.event_stream import RunEventBroker
from gigaloom.settings import HarnessSettingsStore, PersonalizationSettingsStore
from gigaloom.skill_library import SkillLibraryService
from gigaloom.trace_replay import TraceReplayService
from gigaloom.ui.async_execution import AsyncExecutionDiagnostics
from gigaloom.ui.remote_identity import RemoteOIDCClient
from gigaloom.ui.security import HarnessUISecurity
from gigaloom.ui.services import ActiveHeadlessRun
from gigaloom.ui.services.approvals import ApprovalGateService
from gigaloom.ui.services.agent_runtimes import (
    AgentRuntimeWebBundle,
    build_agent_runtime_web_bundle,
)
from gigaloom.ui.services.context_impact import (
    ContextProjectionQuery,
    ImpactProjectionService,
)
from gigaloom.ui.services.credentials import CredentialOperatorService
from gigaloom.ui.services.environment_actions import (
    optional_commit_service,
    optional_pull_request_service,
    optional_push_service,
)
from gigaloom.ui.services.gateway_routes import GatewayRouteWebService
from gigaloom.ui.services.legacy_bundles import (
    LegacyFullBundleCompatibility,
)
from gigaloom.ui.services.mcp_apps import MCPAppHostService
from gigaloom.ui.services.operator_workspace import OperatorEvidenceQuery
from gigaloom.ui.services.operator_arena import ReviewedArenaOwner
from gigaloom.ui.services.operator_terminal import TerminalBrowserOwner
from gigaloom.ui.services.project_catalog import ProjectCatalogWebService
from gigaloom.ui.services.route_advisor import RouteAdvisorWebService
from gigaloom.ui.services.thread_relay import ThreadRelayComposition
from gigaloom.ui.services.run_capsules import (
    OperatorEvidenceObservedInputsProvider,
    RunCapsuleEvidenceQuery,
    SessionLaneDeltaReferenceProvider,
)
from gigaloom.ui.streaming.operator_events import OperatorEventBroker
from gigaloom.workbench_protocol import WorkbenchBackbone
from gigaloom.workbench_resources import (
    WorkbenchPreferenceStore,
    WorkbenchResourceService,
)


@dataclass(frozen=True, slots=True)
class OperationalBackendOwners:
    """Stateful owners shared by later operational product surfaces."""

    credential_broker: InMemoryCredentialBroker
    credential_operator: CredentialOperatorService
    recovery_receipts: RecoveryReceiptService
    lane_delta_builder: LaneDeltaBuilder
    lane_delta_store: FilesystemLaneDeltaPacketStore
    visual_artifact_store: FilesystemVisualArtifactStore
    visual_gate_store: FilesystemVisualGateStore


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
    approval_gate: ApprovalGateService
    attachment_store: FilesystemAttachmentStore
    arena_store: FilesystemHarnessArenaStore
    eval_store: FilesystemHarnessEvalStore
    schedule_service: ScheduleService | None
    project_memory_store: FilesystemProjectMemoryStore
    native_registry: NativeHistoryConnectorRegistry
    native_index_store: NativeSessionIndexStore
    native_process_manager: NativeProcessManager
    async_diagnostics: AsyncExecutionDiagnostics
    legacy_bundle_compatibility: LegacyFullBundleCompatibility
    run_event_broker: RunEventBroker
    workbench_backbone: WorkbenchBackbone
    workbench_resources: WorkbenchResourceService
    settings_store: HarnessSettingsStore
    personalization_store: PersonalizationSettingsStore
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
    context_projection_query: ContextProjectionQuery | None
    impact_projection_service: ImpactProjectionService
    operator_evidence_query: OperatorEvidenceQuery | None
    action_inbox_service: ActionInboxService
    operator_event_broker: OperatorEventBroker
    operational_backends: OperationalBackendOwners
    agent_runtimes: AgentRuntimeWebBundle
    project_catalog_service: ProjectCatalogWebService
    route_advisor_service: RouteAdvisorWebService
    gateway_route_service: GatewayRouteWebService
    thread_relay: ThreadRelayComposition
    mcp_app_host_service: MCPAppHostService
    run_capsule_evidence_query: RunCapsuleEvidenceQuery
    reviewed_arena_owner: ReviewedArenaOwner | None = None
    terminal_browser_owner: TerminalBrowserOwner | None = None
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
            "harness_personalization_store": self.personalization_store,
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
    context_projection_query: ContextProjectionQuery | None = None,
    impact_projection_service: ImpactProjectionService | None = None,
    operator_evidence_query: OperatorEvidenceQuery | None = None,
    action_inbox_service: ActionInboxService | None = None,
    operator_event_broker: OperatorEventBroker | None = None,
    reviewed_arena_owner: ReviewedArenaOwner | None = None,
    terminal_browser_owner: TerminalBrowserOwner | None = None,
    agent_profiles: tuple[AgentProfileV1, ...] | None = None,
    route_recommendation_source: RouteRecommendationSource | None = None,
    run_capsule_capture_ports: RunCapsuleCapturePortsV1 | None = None,
    run_capsule_signer: CapsuleSigner | None = None,
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
    gateway_route_service = GatewayRouteWebService.from_config(
        config,
        process_owner=native_process_manager,
    )
    if config.api_key is None and gateway_route_service.gateway_api_key is not None:
        config = replace(config, api_key=gateway_route_service.gateway_api_key)
    attachment_store = FilesystemAttachmentStore(config.data_dir)
    arena_store = FilesystemHarnessArenaStore(config.data_dir)
    eval_store = FilesystemHarnessEvalStore(config.data_dir)
    memory_store = FilesystemProjectMemoryStore()
    settings_store = HarnessSettingsStore(config.data_dir, config)
    personalization_store = PersonalizationSettingsStore(config.data_dir)
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
    environment_commit_service = optional_commit_service(
        config, environment_commit_service
    )
    environment_push_service = optional_push_service(config, environment_push_service)
    environment_pull_request_service = optional_pull_request_service(
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
    agent_runtimes = build_agent_runtime_web_bundle(
        config, profiles=agent_profiles, reserved_agent_ids=registry.ids()
    )
    registry.bind_dynamic_provider(lambda: acp_harnesses(agent_runtimes.runtime))
    profiles = agent_runtimes.profiles
    lane_delta_builder = LaneDeltaBuilder()
    lane_delta_store = FilesystemLaneDeltaPacketStore(config.data_dir)
    capsule_repository = FilesystemRunCapsuleRepository(config.data_dir)
    capsule_lifecycle = (
        RunCapsuleLifecycleService(
            ports=run_capsule_capture_ports,
            repository=capsule_repository,
            signer=run_capsule_signer,
        )
        if run_capsule_capture_ports is not None
        else None
    )
    lane_delta_lifecycle = LaneDeltaLifecycleService(
        session_store=session_store,
        builder=lane_delta_builder,
        packet_store=lane_delta_store,
        capsule_repository=capsule_repository,
        delegate=capsule_lifecycle,
    )
    runner = HarnessSessionRunner(
        registry=registry,
        config=config,
        store=session_store,
        attachment_store=attachment_store,
        memory_store=memory_store,
        provider_account_provider=native_login_broker,
        run_completion_hook=lane_delta_lifecycle,
        run_lane_lifecycle=lane_delta_lifecycle,
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
        personalization_store=personalization_store,
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
    launch_context = LaunchResolutionContextV1(
        agent_ids=frozenset(
            profile.agent_id for profile in profiles if profile.native is not None
        ),
        structured_route_ids=frozenset(
            route.route_id
            for profile in profiles
            for route in profile.structured_routes
        ),
        terminal_modes=frozenset({"direct", "managed"}),
    )
    project_catalog_service = ProjectCatalogWebService.from_data_dir(
        config.data_dir,
        session_store=session_store,
        launch_context=launch_context,
    )
    route_source = route_recommendation_source or LocalRouteRecommendationSource(
        config.data_dir,
        profiles,
    )
    route_advisor_service = RouteAdvisorWebService(
        advisor=RouteAdvisorApplicationService(route_source),
        repository=RouteDecisionRepository(Path(config.data_dir) / "route-decisions"),
    )
    capsule_evidence_query = RunCapsuleEvidenceQuery(
        capsule_repository,
        OperatorEvidenceObservedInputsProvider(operator_evidence_query),
        SessionLaneDeltaReferenceProvider(session_store, lane_delta_store),
    )
    visual_evidence_root = Path(config.data_dir) / "automation" / "visual-qa-v1"
    credential_broker = InMemoryCredentialBroker("gigaloom-fake-broker-v1")
    thread_relay = ThreadRelayComposition(
        session_store=session_store,
        data_dir=str(config.data_dir),
        turn_submitter=session_service,
        runtime_store=runtime_store,
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
        approval_gate=ApprovalGateService(
            policy_engine=policy_engine,
            runtime_store=runtime_store,
            session_store=session_store,
        ),
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
        legacy_bundle_compatibility=LegacyFullBundleCompatibility(
            store=session_store,
            diagnostics=async_diagnostics,
        ),
        run_event_broker=run_event_broker,
        workbench_backbone=workbench_backbone,
        workbench_resources=workbench_resources,
        settings_store=settings_store,
        personalization_store=personalization_store,
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
        context_projection_query=context_projection_query,
        impact_projection_service=(
            impact_projection_service or ImpactProjectionService()
        ),
        operator_evidence_query=operator_evidence_query,
        action_inbox_service=action_inbox_service or ActionInboxService(()),
        operator_event_broker=operator_event_broker or OperatorEventBroker(),
        operational_backends=OperationalBackendOwners(
            credential_broker=credential_broker,
            credential_operator=CredentialOperatorService.with_fake_broker_demo(
                credential_broker
            ),
            recovery_receipts=RecoveryReceiptService(),
            lane_delta_builder=lane_delta_builder,
            lane_delta_store=lane_delta_store,
            visual_artifact_store=FilesystemVisualArtifactStore(visual_evidence_root),
            visual_gate_store=FilesystemVisualGateStore(visual_evidence_root),
        ),
        agent_runtimes=agent_runtimes,
        project_catalog_service=project_catalog_service,
        route_advisor_service=route_advisor_service,
        gateway_route_service=gateway_route_service,
        thread_relay=thread_relay,
        mcp_app_host_service=MCPAppHostService(),
        run_capsule_evidence_query=capsule_evidence_query,
        reviewed_arena_owner=reviewed_arena_owner,
        terminal_browser_owner=terminal_browser_owner,
    )
