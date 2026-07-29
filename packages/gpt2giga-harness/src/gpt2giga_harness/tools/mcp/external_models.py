"""Contracts for reviewed external MCP catalog selections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from gpt2giga_harness.claude_mcp_target import CLAUDE_MCP_TARGET_ID
from gpt2giga_harness.codex_mcp_target import CODEX_MCP_TARGET_ID
from gpt2giga_harness.gemini_mcp_target import GEMINI_MCP_TARGET_ID
from gpt2giga_harness.integration_packages import (
    InstallationScope,
    IntegrationCompatibility,
    IntegrationComponent,
    IntegrationComponentType,
    IntegrationPackage,
    IntegrationPolicyClass,
    IntegrationRequirement,
    IntegrationRequirementType,
    IntegrationSourceType,
    IntegrationTargetOverlay,
    IntegrationUpdatePolicy,
)
from gpt2giga_harness.secrets import SecretReference, secret_reference_to_dict
from gpt2giga_harness.tools import PolicyDecision, ToolExecutionPolicy

from .contracts import MCPTransport, ToolServerDescriptor
from .external_validation import (
    _canonical_https_origin,
    _json_hash,
    _normalized_names,
    _selection_secret_mapping,
    _validate_argv,
    _validate_secret_bindings,
)


HARNESS_MANAGED_MCP_TARGET_ID = "harness-managed-mcp"
_TARGET_IDS = (
    CODEX_MCP_TARGET_ID,
    CLAUDE_MCP_TARGET_ID,
    GEMINI_MCP_TARGET_ID,
    HARNESS_MANAGED_MCP_TARGET_ID,
)
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EXACT_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,127}\Z")
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_HEADER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}\Z")
_GIT_COMMIT_RE = re.compile(r"(?:commit[:~-]?)?[0-9a-f]{40,64}\Z")


class ExternalMCPSelectionKind(str, Enum):
    """Executable source families admitted from official Registry metadata."""

    PACKAGE = "package"
    GIT = "git"
    REMOTE = "remote"


@dataclass(frozen=True)
class ExternalMCPArtifactResolution:
    """Reviewed immutable package or Git artifact without downloaded bytes."""

    registry_type: str
    identifier: str
    version: str
    immutable_ref: str
    integrity: str
    download_origin: str

    def __post_init__(self) -> None:
        if self.registry_type not in {"npm", "pypi", "nuget", "oci", "mcpb", "git"}:
            raise ValueError("external MCP artifact registry type is unsupported")
        if not self.identifier.strip() or any(
            character in self.identifier for character in ("\0", "\n", "\r")
        ):
            raise ValueError("external MCP artifact identifier is invalid")
        if (
            not _EXACT_VERSION_RE.fullmatch(self.version)
            or self.version.lower() == "latest"
            or any(character in self.version for character in ("*", "^", "<", ">"))
        ):
            raise ValueError("external MCP artifact version must be exact")
        if not self.immutable_ref.strip() or self.immutable_ref.lower() in {
            "latest",
            "main",
            "master",
            "head",
        }:
            raise ValueError("external MCP artifact ref must be immutable")
        if self.registry_type == "git" and not _GIT_COMMIT_RE.fullmatch(
            self.immutable_ref
        ):
            raise ValueError("external MCP Git ref must be an exact commit")
        if not _SHA256_RE.fullmatch(self.integrity):
            raise ValueError("external MCP artifact requires SHA-256 integrity")
        object.__setattr__(
            self,
            "download_origin",
            _canonical_https_origin(self.download_origin),
        )


@dataclass(frozen=True)
class ExternalMCPToolPolicy:
    """Portable tool policy retained without discovered tool content."""

    include_tools: tuple[str, ...] = ()
    exclude_tools: tuple[str, ...] = ()
    default: PolicyDecision = PolicyDecision.ASK

    def __post_init__(self) -> None:
        include = _normalized_names(self.include_tools, "included tool")
        exclude = _normalized_names(self.exclude_tools, "excluded tool")
        if set(include) & set(exclude):
            raise ValueError("external MCP tool policy filters overlap")
        if not isinstance(self.default, PolicyDecision):
            raise ValueError("external MCP default tool policy is invalid")
        object.__setattr__(self, "include_tools", include)
        object.__setattr__(self, "exclude_tools", exclude)


@dataclass(frozen=True)
class ExternalMCPSelection:
    """Explicit operator selection for one Registry package, Git source, or remote."""

    kind: ExternalMCPSelectionKind
    index: int = 0
    artifact: ExternalMCPArtifactResolution | None = None
    launch_argv: tuple[str, ...] = ()
    environment: Mapping[str, SecretReference] = field(default_factory=dict)
    headers: Mapping[str, SecretReference] = field(default_factory=dict)
    timeout_seconds: int = 10
    tool_policy: ExternalMCPToolPolicy = field(default_factory=ExternalMCPToolPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExternalMCPSelectionKind):
            raise ValueError("external MCP selection kind is invalid")
        if (
            isinstance(self.index, bool)
            or not isinstance(self.index, int)
            or self.index < 0
        ):
            raise ValueError("external MCP selection index is invalid")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("external MCP timeout is invalid")
        object.__setattr__(self, "launch_argv", _validate_argv(self.launch_argv))
        object.__setattr__(
            self,
            "environment",
            _validate_secret_bindings(self.environment, _ENV_RE, "environment"),
        )
        object.__setattr__(
            self,
            "headers",
            _validate_secret_bindings(self.headers, _HEADER_RE, "header"),
        )
        if self.kind is ExternalMCPSelectionKind.REMOTE:
            if self.artifact is not None or self.launch_argv or self.environment:
                raise ValueError("remote MCP selection cannot contain package fields")
        elif self.artifact is None or not self.launch_argv:
            raise ValueError("package and Git MCP selections require artifact and argv")


def external_mcp_selection_from_dict(value: Any) -> ExternalMCPSelection:
    """Parse one strict content-free operator selection from an API payload."""
    if not isinstance(value, Mapping):
        raise ValueError("external MCP selection must be an object")
    allowed = {
        "kind",
        "index",
        "artifact",
        "launch_argv",
        "environment",
        "headers",
        "timeout_seconds",
        "tool_policy",
    }
    if set(value) - allowed:
        raise ValueError("external MCP selection contains unknown fields")
    artifact_value = value.get("artifact")
    artifact = None
    if artifact_value is not None:
        if not isinstance(artifact_value, Mapping):
            raise ValueError("external MCP artifact must be an object")
        artifact_fields = {
            "registry_type",
            "identifier",
            "version",
            "immutable_ref",
            "integrity",
            "download_origin",
        }
        if set(artifact_value) != artifact_fields:
            raise ValueError("external MCP artifact fields are invalid")
        artifact = ExternalMCPArtifactResolution(
            **{key: str(artifact_value[key]) for key in sorted(artifact_fields)}
        )
    policy_value = value.get("tool_policy", {})
    if not isinstance(policy_value, Mapping) or set(policy_value) - {
        "include_tools",
        "exclude_tools",
        "default",
    }:
        raise ValueError("external MCP tool policy is invalid")
    launch_argv = value.get("launch_argv", ())
    include_tools = policy_value.get("include_tools", ())
    exclude_tools = policy_value.get("exclude_tools", ())
    if not isinstance(launch_argv, (list, tuple)):
        raise ValueError("external MCP launch_argv must be a list")
    if not isinstance(include_tools, (list, tuple)) or not isinstance(
        exclude_tools, (list, tuple)
    ):
        raise ValueError("external MCP tool filters must be lists")
    try:
        kind = ExternalMCPSelectionKind(str(value.get("kind") or ""))
        default = PolicyDecision(str(policy_value.get("default") or "ask"))
    except ValueError as exc:
        raise ValueError("external MCP selection enum is invalid") from exc
    return ExternalMCPSelection(
        kind=kind,
        index=value.get("index", 0),
        artifact=artifact,
        launch_argv=tuple(str(item) for item in launch_argv),
        environment=_selection_secret_mapping(
            value.get("environment", {}), "environment"
        ),
        headers=_selection_secret_mapping(value.get("headers", {}), "header"),
        timeout_seconds=value.get("timeout_seconds", 10),
        tool_policy=ExternalMCPToolPolicy(
            include_tools=tuple(str(item) for item in include_tools),
            exclude_tools=tuple(str(item) for item in exclude_tools),
            default=default,
        ),
    )


@dataclass(frozen=True)
class ExternalMCPDescriptor:
    """Canonical reviewed MCP descriptor shared by every target projection."""

    id: str
    official_name: str
    version: str
    title: str
    description: str
    catalog_id: str
    immutable_ref: str
    content_hash: str
    transport: MCPTransport
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    url: str | None
    environment: Mapping[str, SecretReference]
    headers: Mapping[str, SecretReference]
    artifact: ExternalMCPArtifactResolution | None
    network_origins: tuple[str, ...]
    timeout_seconds: int
    tool_policy: ExternalMCPToolPolicy
    discovery_source_id: str | None = None

    @property
    def semantic_hash(self) -> str:
        """Return a deterministic content hash without resolving secrets."""
        return _json_hash(external_mcp_descriptor_to_dict(self))

    def to_harness_descriptor(
        self, *, trusted: bool = False, enabled: bool = False
    ) -> ToolServerDescriptor:
        """Project into the existing managed MCP descriptor/snapshot contract."""
        return ToolServerDescriptor(
            id=self.id,
            title=self.title,
            description=self.description,
            transport=self.transport,
            command=self.command,
            args=self.args,
            cwd=self.cwd,
            url=self.url,
            environment=self.environment,
            headers=self.headers,
            source=f"official-mcp-registry:{self.catalog_id}",
            trusted=trusted,
            enabled=enabled,
            timeout_seconds=float(self.timeout_seconds),
            harnesses=("codex-cli", "claude-code", "gemini-cli"),
            execution_policy=ToolExecutionPolicy(
                id=f"external-mcp-{self.semantic_hash[:16]}",
                default=self.tool_policy.default,
            ),
        )

    def to_integration_package(self) -> IntegrationPackage:
        """Declare every reviewed effect without authorizing installation."""
        requirements: list[IntegrationRequirement] = []
        if self.artifact is not None:
            requirements.append(
                IntegrationRequirement(
                    id="artifact",
                    type=IntegrationRequirementType.PACKAGE,
                    classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                    reason="Acquire the reviewed immutable MCP artifact.",
                    locator=(
                        f"{self.artifact.registry_type}:{self.artifact.identifier}"
                        f"@{self.artifact.version}"
                    ),
                    checksum=self.artifact.integrity,
                )
            )
        if self.command is not None:
            requirements.append(
                IntegrationRequirement(
                    id="command",
                    type=IntegrationRequirementType.COMMAND,
                    classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                    reason="Start the reviewed MCP server with exact argv.",
                    argv=(self.command, *self.args),
                    environment=tuple(self.environment),
                )
            )
        for index, origin in enumerate(self.network_origins, start=1):
            requirements.append(
                IntegrationRequirement(
                    id=f"network-{index}",
                    type=IntegrationRequirementType.NETWORK,
                    classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                    reason="Use a declared MCP artifact or server origin.",
                    locator=origin,
                )
            )
        for index, name in enumerate(
            sorted(set(self.environment) | set(self.headers)), start=1
        ):
            requirements.append(
                IntegrationRequirement(
                    id=f"secret-{index}",
                    type=IntegrationRequirementType.SECRET,
                    classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                    reason=f"Resolve the reviewed secret reference for {name}.",
                    secret_owner="gpt2giga-harness",
                )
            )
        requirements.append(
            IntegrationRequirement(
                id="target-config-write",
                type=IntegrationRequirementType.PERMISSION,
                classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                reason="Write only the selected target configuration under N4-02 ownership.",
            )
        )
        source_type = (
            IntegrationSourceType.GIT
            if self.artifact is not None and self.artifact.registry_type == "git"
            else IntegrationSourceType.PACKAGE
            if self.artifact is not None
            else IntegrationSourceType.RAW_MCP
        )
        source = (
            self.artifact.identifier if self.artifact is not None else str(self.url)
        )
        requirement_ids = tuple(item.id for item in requirements)
        return IntegrationPackage(
            id=self.id,
            version=self.version,
            publisher="official-mcp-registry",
            license="NOASSERTION",
            source_type=source_type,
            source=source,
            immutable_ref=(
                self.artifact.immutable_ref
                if self.artifact is not None
                else self.immutable_ref
            ),
            checksum=(
                self.artifact.integrity
                if self.artifact is not None
                else f"sha256:{self.content_hash}"
            ),
            components=(
                IntegrationComponent(
                    id="portable-mcp",
                    type=IntegrationComponentType.MCP,
                    portable=True,
                ),
            ),
            requirements=tuple(requirements),
            overlays=tuple(
                IntegrationTargetOverlay(
                    target_id=target_id,
                    component_ids=("portable-mcp",),
                    requirement_ids=requirement_ids,
                )
                for target_id in _TARGET_IDS
            ),
            compatibility=tuple(
                IntegrationCompatibility(target_id=target_id)
                for target_id in _TARGET_IDS
            ),
            scopes=(InstallationScope.MANAGED_HOME, InstallationScope.PROJECT),
            update_policy=IntegrationUpdatePolicy.PINNED,
            verification_steps=("bounded-native-discovery", "content-free-probe"),
            rollback_steps=("transactional-owner-restore",),
        )


@dataclass(frozen=True)
class ExternalMCPTargetPreview:
    """Deterministic target result including unsupported capability evidence."""

    plan_id: str
    target_id: str
    descriptor_sha256: str
    supported: bool
    error_code: str | None
    configuration: Mapping[str, Any]
    commands: tuple[tuple[str, ...], ...]
    packages: tuple[Mapping[str, str], ...]
    network_origins: tuple[str, ...]
    filesystem_permissions: tuple[str, ...]
    secret_references: tuple[Mapping[str, Any], ...]
    native_consent_required: bool
    restart_required: bool
    install_authorized: bool = False


def external_mcp_descriptor_to_dict(
    descriptor: ExternalMCPDescriptor,
) -> dict[str, Any]:
    """Serialize one descriptor without resolving any secret reference."""
    artifact = descriptor.artifact
    return {
        "schema_version": 1,
        "id": descriptor.id,
        "official_name": descriptor.official_name,
        "version": descriptor.version,
        "title": descriptor.title,
        "description": descriptor.description,
        "catalog_id": descriptor.catalog_id,
        "immutable_ref": descriptor.immutable_ref,
        "content_hash": descriptor.content_hash,
        "transport": descriptor.transport.value,
        "command": descriptor.command,
        "args": list(descriptor.args),
        "cwd": descriptor.cwd,
        "url": descriptor.url,
        "environment": {
            key: secret_reference_to_dict(value)
            for key, value in sorted(descriptor.environment.items())
        },
        "headers": {
            key: secret_reference_to_dict(value)
            for key, value in sorted(descriptor.headers.items())
        },
        "artifact": (
            {
                "registry_type": artifact.registry_type,
                "identifier": artifact.identifier,
                "version": artifact.version,
                "immutable_ref": artifact.immutable_ref,
                "integrity": artifact.integrity,
                "download_origin": artifact.download_origin,
            }
            if artifact is not None
            else None
        ),
        "network_origins": list(descriptor.network_origins),
        "timeout_seconds": descriptor.timeout_seconds,
        "tool_policy": {
            "include_tools": list(descriptor.tool_policy.include_tools),
            "exclude_tools": list(descriptor.tool_policy.exclude_tools),
            "default": descriptor.tool_policy.default.value,
        },
        "discovery_source_id": descriptor.discovery_source_id,
        "install_authorized": False,
    }
