"""Bounded HTTP contracts for the coding-agent registry marketplace."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Identity = str
Digest = str


class AgentDistributionResponse(BaseModel):
    kind: Literal["binary", "npx", "uvx"]
    platform: str
    architecture: str
    integrity: Literal["verified", "unverified"]
    package_or_archive: str


class AgentRegistryEntryResponse(BaseModel):
    registry_id: Identity
    name: str
    version: str
    description: str
    license: str
    repository: str | None
    website: str | None
    entry_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    platforms: list[str] = Field(max_length=16)
    distribution_kinds: list[Literal["binary", "npx", "uvx"]] = Field(max_length=3)
    integrity: Literal["verified", "unverified", "mixed"]
    distributions: list[AgentDistributionResponse] = Field(max_length=32)


class AgentRuntimeReadinessResponse(BaseModel):
    schema_version: Literal[1] = 1
    status: Literal["ready", "native-only", "reprobe", "blocked"]
    acp_transport: Literal["ready", "blocked"]
    provider_bridge: Literal["ready", "native-only", "reprobe", "blocked"]
    protocols: list[str] = Field(max_length=32)
    gateway_availability: Literal["available", "unsupported", "reprobe", "blocked"]
    native_launch_available: bool
    reason_ids: list[str] = Field(max_length=32)
    action: Literal[
        "select_gateway_route", "use_native", "reprobe", "activate", "inspect"
    ]


class InstalledAgentResponse(BaseModel):
    local_agent_id: Identity
    registry_id: Identity
    version: str
    install_id: Identity
    distribution_kind: Literal["binary", "npx", "uvx"]
    active: bool
    activation_status: str
    probe_state: str
    auth_required: bool
    update_available: bool
    readiness: AgentRuntimeReadinessResponse


class LocalManifestResponse(BaseModel):
    agent_id: Identity
    display_name: str
    profile_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    source: str
    structured_route_ids: list[str] = Field(max_length=32)
    native_available: bool


class AgentRegistryInventoryResponse(BaseModel):
    schema_version: Literal[1] = 1
    snapshot_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    fetched_at: str
    stale: bool
    offline: bool
    from_cache: bool
    refresh_error_code: str | None
    explicitly_refreshed: bool
    registry_entries: list[AgentRegistryEntryResponse] = Field(max_length=100)
    installed: list[InstalledAgentResponse] = Field(max_length=256)
    local_manifests: list[LocalManifestResponse] = Field(max_length=100)
    install_decisions_browser_owned: Literal[False] = False


class AgentInstallPreviewRequest(BaseModel):
    registry_query: Identity = Field(min_length=1, max_length=256)
    local_agent_id: Identity | None = Field(default=None, max_length=256)


class AgentDistributionDecisionResponse(BaseModel):
    distribution_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    rank: int = Field(ge=0, le=10_000)
    status: Literal["selected", "rejected"]
    reason_code: str


class AgentInstallPlanResponse(BaseModel):
    plan_id: Identity
    registry_id: Identity
    entry_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    local_agent_id: Identity
    version: str
    platform: str
    architecture: str
    distribution_kind: Literal["binary", "npx", "uvx"]
    package_or_archive: str
    integrity_policy: str
    lifecycle_script_policy: str
    side_effects: list[str] = Field(max_length=16)
    confirmation_required: Literal[True]
    expires_at: str


class AgentInstallPreviewResponse(BaseModel):
    schema_version: Literal[1] = 1
    reason_code: str
    local_agent_id: str | None
    proposed_local_agent_id: str | None
    collision_namespaces: list[str] = Field(max_length=8)
    decisions: list[AgentDistributionDecisionResponse] = Field(max_length=32)
    plan: AgentInstallPlanResponse | None
    installation_started: Literal[False] = False
    browser_selected_distribution: Literal[False] = False


class AgentInstallStartRequest(BaseModel):
    registry_query: Identity = Field(min_length=1, max_length=256)
    local_agent_id: Identity | None = Field(default=None, max_length=256)
    expected_plan_id: Identity = Field(pattern=r"^plan-[0-9a-f]{24}$")
    allow_unverified: bool = False
    confirmed: Literal[True]


class AgentUpdateStartRequest(BaseModel):
    allow_unverified: bool = False
    confirmed: Literal[True]


class AgentConfirmedActionRequest(BaseModel):
    confirmed: Literal[True]


class AgentActivateRequest(BaseModel):
    install_id: Identity | None = Field(default=None, max_length=256)
    confirmed: Literal[True]


class AgentInstallationEventResponse(BaseModel):
    sequence: int = Field(ge=0, le=63)
    state: str
    reason_code: str
    observed_at: str


class AgentInstallationOperationResponse(BaseModel):
    schema_version: Literal[1] = 1
    operation_id: Identity
    kind: Literal["install", "update"]
    registry_or_local_id: Identity
    requested_local_agent_id: Identity | None
    status: str
    terminal: bool
    events: list[AgentInstallationEventResponse] = Field(max_length=64)
    result_local_agent_id: Identity | None
    result_install_id: Identity | None
    result_version: str | None
    result_active: bool | None
    terminal_reason_code: str | None
    content_free: Literal[True]


class AgentProbeResponse(BaseModel):
    schema_version: Literal[1] = 1
    state: str
    protocol_state: str
    protocol_version: str | None
    auth_methods: list[str] = Field(max_length=32)
    capabilities: list[str] = Field(max_length=128)
    losses: list[str] = Field(max_length=128)
    warnings: list[str] = Field(max_length=128)
    native_home_isolated: bool
    network_policy: str
    readiness: AgentRuntimeReadinessResponse
    content_free: Literal[True]


class AgentActivationResponse(BaseModel):
    schema_version: Literal[1] = 1
    local_agent_id: Identity
    registry_id: Identity
    version: str
    install_id: Identity
    active: bool
    activation_status: str
    compatibility_status: str
    probe: AgentProbeResponse
    omissions: list[str] = Field(max_length=32)
    atomic: Literal[True]
    content_free: Literal[True]


class AgentRollbackResponse(BaseModel):
    schema_version: Literal[1] = 1
    local_agent_id: Identity
    install_id: Identity
    previous_install_id: Identity | None
    status: str
    atomic: Literal[True]


class AgentRemoveResponse(BaseModel):
    schema_version: Literal[1] = 1
    local_agent_id: Identity
    removed_install_count: int = Field(ge=0, le=256)
    native_or_provider_artifacts_removed: Literal[False] = False


class AgentUseResponse(BaseModel):
    schema_version: Literal[1] = 1
    local_agent_id: Identity
    href: str
    run_started: Literal[False] = False


class AgentRecoveryResponse(BaseModel):
    schema_version: Literal[1] = 1
    recovered_operation_ids: list[Identity] = Field(max_length=256)
    recovered_plan_ids: list[Identity] = Field(max_length=256)
    cleanup_statuses: list[str] = Field(max_length=256)


__all__ = [
    "AgentActivateRequest",
    "AgentActivationResponse",
    "AgentConfirmedActionRequest",
    "AgentInstallPreviewRequest",
    "AgentInstallPreviewResponse",
    "AgentInstallStartRequest",
    "AgentInstallationOperationResponse",
    "AgentProbeResponse",
    "AgentRecoveryResponse",
    "AgentRegistryInventoryResponse",
    "AgentRuntimeReadinessResponse",
    "AgentRemoveResponse",
    "AgentRollbackResponse",
    "AgentUpdateStartRequest",
    "AgentUseResponse",
]
