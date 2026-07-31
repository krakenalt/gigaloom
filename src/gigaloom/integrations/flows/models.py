# ruff: noqa: E402, F401, F403, F405
"""Integration flow records and built-in target descriptors."""

from __future__ import annotations

from .dependencies import *  # noqa: F403


class IntegrationFlowError(RuntimeError):
    """Base error for application-owned integration flows."""


class IntegrationFlowNotFoundError(IntegrationFlowError):
    """Raised when a flow id does not exist."""


class IntegrationFlowConflictError(IntegrationFlowError):
    """Raised when an approval or lifecycle transition is stale."""


class IntegrationFlowSource(str, Enum):
    """Product source choices exposed consistently to Web, CLI, and API."""

    CATALOG = "catalog"
    MARKETPLACE = "marketplace"
    GIT = "git"
    LOCAL = "local"
    PACKAGE = "package"
    RAW_DESCRIPTOR = "raw_descriptor"


class IntegrationFlowStatus(str, Enum):
    """Durable operation states rendered by every client."""

    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    VERIFIED = "verified"
    HANDOFF_REQUIRED = "handoff_required"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class IntegrationFlowEvent:
    """Content-free progress event for one bounded lifecycle stage."""

    stage: str
    status: str
    occurred_at: str
    code: str | None = None


@dataclass(frozen=True)
class IntegrationFlowRecord:
    """Private durable flow record with a public content-free projection."""

    id: str
    plan_id: str
    status: IntegrationFlowStatus
    source: IntegrationFlowSource
    package_id: str
    package_version: str
    manifest_sha256: str
    target_id: str
    scope: InstallationScope
    workspace: str | None
    request: Mapping[str, Any]
    receipt_id: str | None
    verification_status: str
    rollback_available: bool
    error_code: str | None
    created_at: str
    updated_at: str
    events: tuple[IntegrationFlowEvent, ...]
    source_provenance: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class _ResolvedPreview:
    """Internal exact preview plus its public application projection."""

    package: IntegrationPackage
    target: ExtensionTargetDescriptor
    root: Path
    executable: bool
    execution_owner: str
    native_plan_id: str | None
    configuration_diff: tuple[str, ...]
    restart_required: bool
    handoff_reason: str | None = None
    configuration_preview: Mapping[str, Any] | None = None
    source_provenance: Mapping[str, Any] | None = None


_SKILL_TARGETS = {
    CODEX_SKILL_TARGET_ID: ExtensionTargetDescriptor(
        id=CODEX_SKILL_TARGET_ID,
        revision="1",
        component_types=(IntegrationComponentType.SKILL,),
        scopes=(InstallationScope.MANAGED_HOME, InstallationScope.PROJECT),
        capabilities=("install", "verify", "rollback", "skill.discovery"),
        trust_evidence=(
            IntegrationTrustEvidence(
                id="codex-skill-documented-surface",
                kind=IntegrationTrustKind.SOURCE,
                status=IntegrationTrustStatus.VERIFIED,
                authority="agentskills-open-standard",
                revision="2026-07-19",
            ),
        ),
    ),
    CLAUDE_SKILL_TARGET_ID: ExtensionTargetDescriptor(
        id=CLAUDE_SKILL_TARGET_ID,
        revision="1",
        component_types=(IntegrationComponentType.SKILL,),
        scopes=(InstallationScope.MANAGED_HOME, InstallationScope.PROJECT),
        capabilities=("install", "verify", "rollback", "skill.discovery"),
        trust_evidence=(
            IntegrationTrustEvidence(
                id="claude-skill-documented-surface",
                kind=IntegrationTrustKind.SOURCE,
                status=IntegrationTrustStatus.VERIFIED,
                authority="agentskills-open-standard",
                revision="2026-07-19",
            ),
        ),
    ),
    GEMINI_SKILL_TARGET_ID: ExtensionTargetDescriptor(
        id=GEMINI_SKILL_TARGET_ID,
        revision="1",
        component_types=(IntegrationComponentType.SKILL,),
        scopes=(InstallationScope.MANAGED_HOME, InstallationScope.PROJECT),
        capabilities=(
            "install",
            "provider_consent",
            "rollback",
            "skill.discovery",
            "verify",
        ),
        trust_evidence=(
            IntegrationTrustEvidence(
                id="gemini-skill-documented-surface",
                kind=IntegrationTrustKind.SOURCE,
                status=IntegrationTrustStatus.VERIFIED,
                authority="agentskills-open-standard",
                revision="2026-07-19",
            ),
        ),
    ),
}

_PLUGIN_TARGET_IDS = {
    CODEX_PLUGIN_TARGET_ID,
    CLAUDE_PLUGIN_TARGET_ID,
    GEMINI_EXTENSION_TARGET_ID,
}

HARNESS_PACKAGE_TARGET = ExtensionTargetDescriptor(
    id="harness-adapter-package",
    revision="1",
    component_types=(IntegrationComponentType.HARNESS_ADAPTER,),
    scopes=(InstallationScope.MANAGED_HOME,),
    capabilities=("package_handoff", "preview", "sdk_conformance_required"),
    trust_evidence=(
        IntegrationTrustEvidence(
            id="harness-adapter-sdk-surface",
            kind=IntegrationTrustKind.SOURCE,
            status=IntegrationTrustStatus.VERIFIED,
            authority="gigaloom-sdk",
            revision="1",
        ),
    ),
)

HARNESS_MANAGED_MCP_TARGET = ExtensionTargetDescriptor(
    id=HARNESS_MANAGED_MCP_TARGET_ID,
    revision="1",
    component_types=(IntegrationComponentType.MCP,),
    scopes=(InstallationScope.MANAGED_HOME,),
    capabilities=("install", "verify", "rollback", "managed-mcp.inventory"),
    trust_evidence=(
        IntegrationTrustEvidence(
            id="harness-managed-mcp-inventory",
            kind=IntegrationTrustKind.SOURCE,
            status=IntegrationTrustStatus.VERIFIED,
            authority="gigaloom",
            revision="1",
        ),
    ),
)

_MCP_TARGET_IDS = {
    CODEX_MCP_TARGET_ID,
    CLAUDE_MCP_TARGET_ID,
    GEMINI_MCP_TARGET_ID,
    HARNESS_MANAGED_MCP_TARGET_ID,
}

BUILTIN_FLOW_TARGETS = tuple(
    sorted(
        (
            CODEX_MCP_TARGET_DESCRIPTOR,
            CLAUDE_MCP_TARGET_DESCRIPTOR,
            GEMINI_MCP_TARGET_DESCRIPTOR,
            CODEX_PLUGIN_TARGET_DESCRIPTOR,
            CLAUDE_PLUGIN_TARGET_DESCRIPTOR,
            GEMINI_EXTENSION_TARGET_DESCRIPTOR,
            HARNESS_MANAGED_MCP_TARGET,
            *_SKILL_TARGETS.values(),
            HARNESS_PACKAGE_TARGET,
        ),
        key=lambda item: item.id,
    )
)

__all__ = [name for name in globals() if not name.startswith("__")]
