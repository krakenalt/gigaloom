"""Projection and transport handoff for reviewed external MCP entries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

from gigaloom.claude_mcp_target import (
    CLAUDE_MCP_TARGET_ID,
    ClaudeMCPServerSpec,
    ClaudeMCPTransport,
)
from gigaloom.codex_mcp_target import (
    CODEX_MCP_TARGET_ID,
    CodexMCPDefaultApproval,
    CodexMCPServerSpec,
    CodexMCPTransport,
)
from gigaloom.gemini_mcp_target import (
    GEMINI_MCP_TARGET_ID,
    GeminiMCPServerSpec,
    GeminiMCPTransport,
)
from gigaloom.integration_catalog import (
    CatalogEntry,
    CatalogSourceType,
)
from gigaloom.secrets import (
    SecretReference,
    SecretReferenceKind,
    secret_reference_to_dict,
)
from gigaloom.tools import PolicyDecision

from .contracts import MCPTransport
from .external_models import (
    HARNESS_MANAGED_MCP_TARGET_ID,
    ExternalMCPArtifactResolution,
    ExternalMCPDescriptor,
    ExternalMCPSelection,
    ExternalMCPSelectionKind,
    ExternalMCPTargetPreview,
    external_mcp_descriptor_to_dict,
    _ENV_RE,
    _HEADER_RE,
    _TARGET_IDS,
)
from .external_validation import (
    _IMPLICIT_INSTALLERS,
    _canonical_git_url,
    _canonical_https_url,
    _json_hash,
    _origin,
    _target_safe_id,
)


def normalize_external_mcp_candidate(
    official_entry: CatalogEntry,
    selection: ExternalMCPSelection,
    *,
    discovery_entry: CatalogEntry | None = None,
) -> ExternalMCPDescriptor:
    """Normalize one reviewed official pin; discovery metadata remains non-authoritative."""
    server = _official_server(official_entry)
    discovery_source_id = _validate_discovery(discovery_entry, official_entry)
    name = str(server["name"])
    title = str(server.get("title") or name.rsplit("/", 1)[-1])
    description = str(server["description"])
    environment: Mapping[str, SecretReference]
    headers: Mapping[str, SecretReference]
    artifact = selection.artifact
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    origins: set[str] = set()

    if selection.kind is ExternalMCPSelectionKind.REMOTE:
        remote = _selected_record(server, "remotes", selection.index)
        if remote.get("type") != "streamable-http":
            raise ValueError("external MCP remote transport is unsupported")
        if remote.get("variables"):
            raise ValueError("external MCP URL templates require an explicit handoff")
        url = _canonical_https_url(remote.get("url"))
        environment = {}
        headers = _declared_secret_bindings(
            remote.get("headers", ()), selection.headers, "header"
        )
        origins.add(_origin(url))
        transport = MCPTransport.STREAMABLE_HTTP
    else:
        if selection.kind is ExternalMCPSelectionKind.PACKAGE:
            package = _selected_record(server, "packages", selection.index)
            _match_package_resolution(package, artifact)
            declared_environment = package.get("environmentVariables", ())
            if package.get("packageArguments") or package.get("runtimeArguments"):
                raise ValueError(
                    "external MCP templated package arguments require an explicit handoff"
                )
        else:
            _match_git_resolution(server, artifact)
            declared_environment = ()
        _validate_launch_binding(selection.launch_argv, artifact)
        environment = _declared_secret_bindings(
            declared_environment, selection.environment, "environment"
        )
        if selection.headers:
            raise ValueError("stdio MCP selection cannot contain headers")
        headers = {}
        command, *raw_args = selection.launch_argv
        args = tuple(raw_args)
        origins.add(artifact.download_origin)
        transport = MCPTransport.STDIO

    return ExternalMCPDescriptor(
        id=_target_safe_id(name),
        official_name=name,
        version=str(server["version"]),
        title=title,
        description=description,
        catalog_id=official_entry.catalog_id,
        immutable_ref=str(official_entry.immutable_ref),
        content_hash=official_entry.content_hash,
        transport=transport,
        command=command,
        args=args,
        cwd=None,
        url=url,
        environment=environment,
        headers=headers,
        artifact=artifact,
        network_origins=tuple(sorted(origins)),
        timeout_seconds=selection.timeout_seconds,
        tool_policy=selection.tool_policy,
        discovery_source_id=discovery_source_id,
    )


def project_external_mcp_target(
    descriptor: ExternalMCPDescriptor, target_id: str
) -> ExternalMCPTargetPreview:
    """Create one exact target preview without installation or provider I/O."""
    if target_id not in _TARGET_IDS:
        raise ValueError("external MCP target is unsupported")
    error_code: str | None = None
    configuration: Mapping[str, Any] = {}
    native = target_id != HARNESS_MANAGED_MCP_TARGET_ID
    if native and any(
        reference.kind is not SecretReferenceKind.ENVIRONMENT
        for reference in (
            *descriptor.environment.values(),
            *descriptor.headers.values(),
        )
    ):
        error_code = "target.secret_backend_unsupported"
    elif native and any(
        name != reference.name for name, reference in descriptor.environment.items()
    ):
        error_code = "target.environment_alias_unsupported"
    elif descriptor.transport is MCPTransport.SSE and target_id != GEMINI_MCP_TARGET_ID:
        error_code = "target.transport_unsupported"
    elif descriptor.cwd is not None and target_id == CLAUDE_MCP_TARGET_ID:
        error_code = "target.cwd_unsupported"
    elif target_id == CLAUDE_MCP_TARGET_ID and (
        descriptor.tool_policy.include_tools
        or descriptor.tool_policy.exclude_tools
        or descriptor.tool_policy.default is not PolicyDecision.ASK
    ):
        error_code = "target.tool_policy_unsupported"
    elif target_id in {CODEX_MCP_TARGET_ID, GEMINI_MCP_TARGET_ID} and (
        descriptor.tool_policy.default is PolicyDecision.DENY
        or (
            target_id == GEMINI_MCP_TARGET_ID
            and descriptor.tool_policy.default is PolicyDecision.ALLOW
        )
    ):
        error_code = "target.default_policy_unsupported"
    else:
        configuration = _target_configuration(descriptor, target_id)

    artifact = descriptor.artifact
    packages = (
        (
            {
                "registry_type": artifact.registry_type,
                "identifier": artifact.identifier,
                "version": artifact.version,
                "immutable_ref": artifact.immutable_ref,
                "integrity": artifact.integrity,
                "download_origin": artifact.download_origin,
            },
        )
        if artifact is not None
        else ()
    )
    secrets = tuple(
        {
            "field": field,
            "name": name,
            "reference": secret_reference_to_dict(reference),
        }
        for field, bindings in (
            ("environment", descriptor.environment),
            ("header", descriptor.headers),
        )
        for name, reference in sorted(bindings.items())
    )
    payload = {
        "target_id": target_id,
        "descriptor_sha256": descriptor.semantic_hash,
        "supported": error_code is None,
        "error_code": error_code,
        "configuration": configuration,
        "commands": (
            [[descriptor.command, *descriptor.args]] if descriptor.command else []
        ),
        "packages": list(packages),
        "network_origins": list(descriptor.network_origins),
        "filesystem_permissions": [
            "write:harness-private-artifact-cache" if artifact is not None else "none",
            f"write:{target_id}:managed-configuration",
        ],
        "secret_references": list(secrets),
        "native_consent_required": native,
        "restart_required": native,
        "install_authorized": False,
    }
    return ExternalMCPTargetPreview(
        plan_id=f"plan_{_json_hash(payload)}",
        target_id=target_id,
        descriptor_sha256=descriptor.semantic_hash,
        supported=error_code is None,
        error_code=error_code,
        configuration=configuration,
        commands=(
            ((descriptor.command, *descriptor.args),) if descriptor.command else ()
        ),
        packages=packages,
        network_origins=descriptor.network_origins,
        filesystem_permissions=tuple(payload["filesystem_permissions"]),
        secret_references=secrets,
        native_consent_required=native,
        restart_required=native,
    )


def external_mcp_server_spec(
    descriptor: ExternalMCPDescriptor, target_id: str
) -> CodexMCPServerSpec | ClaudeMCPServerSpec | GeminiMCPServerSpec:
    """Return the validated native server spec used by one supported target."""
    if target_id == HARNESS_MANAGED_MCP_TARGET_ID:
        raise ValueError("Harness-managed MCP inventory has no native server spec")
    preview = project_external_mcp_target(descriptor, target_id)
    if not preview.supported:
        raise ValueError(
            f"external MCP target is incompatible: {preview.error_code or 'unknown'}"
        )
    configuration = dict(preview.configuration)
    configuration["args"] = tuple(configuration["args"])
    configuration["env_vars"] = tuple(configuration["env_vars"])
    configuration["env_http_headers"] = tuple(
        tuple(item) for item in configuration["env_http_headers"]
    )
    if target_id == CODEX_MCP_TARGET_ID:
        configuration["transport"] = CodexMCPTransport(configuration["transport"])
        configuration["enabled_tools"] = tuple(configuration["enabled_tools"])
        configuration["disabled_tools"] = tuple(configuration["disabled_tools"])
        configuration["default_tools_approval_mode"] = CodexMCPDefaultApproval(
            configuration["default_tools_approval_mode"]
        )
        return CodexMCPServerSpec(**configuration)
    if target_id == CLAUDE_MCP_TARGET_ID:
        configuration["transport"] = ClaudeMCPTransport(configuration["transport"])
        return ClaudeMCPServerSpec(**configuration)
    if target_id == GEMINI_MCP_TARGET_ID:
        configuration["transport"] = GeminiMCPTransport(configuration["transport"])
        configuration["include_tools"] = tuple(configuration["include_tools"])
        configuration["exclude_tools"] = tuple(configuration["exclude_tools"])
        return GeminiMCPServerSpec(**configuration)
    raise ValueError("external MCP target is unsupported")


def _official_server(entry: CatalogEntry) -> Mapping[str, Any]:
    if (
        entry.source_type is not CatalogSourceType.OFFICIAL_MCP_REGISTRY
        or entry.mcp_response is None
        or not entry.pinned
        or entry.immutable_ref is None
    ):
        raise ValueError(
            "external MCP normalization requires an official immutable pin"
        )
    server = entry.mcp_response.get("server")
    if not isinstance(server, Mapping):
        raise ValueError("official MCP server metadata is invalid")
    return server


def _validate_discovery(
    discovery: CatalogEntry | None, official: CatalogEntry
) -> str | None:
    if discovery is None:
        return None
    metadata = discovery.federated
    if (
        discovery.source_type is not CatalogSourceType.FEDERATED_CATALOG
        or metadata is None
        or metadata.component != "mcp"
        or metadata.canonical_package_id != official.package_id
        or discovery.package_id != official.package_id
        or discovery.package is not None
        or discovery.pinned
        or discovery.install_authorized
    ):
        raise ValueError("federated MCP discovery identity does not match official pin")
    return discovery.source_id


def _selected_record(
    server: Mapping[str, Any], field_name: str, index: int
) -> Mapping[str, Any]:
    records = server.get(field_name)
    if not isinstance(records, list) or not records or len(records) > 64:
        raise ValueError(f"official MCP {field_name} are unavailable or invalid")
    if index >= len(records) or not isinstance(records[index], Mapping):
        raise ValueError(f"official MCP {field_name} selection is invalid")
    return records[index]


def _match_package_resolution(
    package: Mapping[str, Any], artifact: ExternalMCPArtifactResolution | None
) -> None:
    if artifact is None:
        raise ValueError("external MCP package resolution is required")
    transport = package.get("transport")
    if not isinstance(transport, Mapping) or transport.get("type") != "stdio":
        raise ValueError("external MCP package transport is unsupported")
    if (
        package.get("registryType") != artifact.registry_type
        or package.get("identifier") != artifact.identifier
        or package.get("version") != artifact.version
    ):
        raise ValueError("external MCP artifact does not match official package pin")
    declared_hash = package.get("fileSha256")
    if declared_hash is not None and f"sha256:{declared_hash}" != artifact.integrity:
        raise ValueError("external MCP artifact integrity conflicts with official pin")


def _match_git_resolution(
    server: Mapping[str, Any], artifact: ExternalMCPArtifactResolution | None
) -> None:
    if artifact is None or artifact.registry_type != "git":
        raise ValueError("external MCP Git resolution is required")
    repository = server.get("repository")
    if not isinstance(repository, Mapping) or repository.get("source") != "github":
        raise ValueError("official MCP Git repository is unavailable")
    if _canonical_git_url(repository.get("url")) != _canonical_git_url(
        artifact.identifier
    ):
        raise ValueError("external MCP Git artifact does not match official identity")
    if artifact.version != server.get("version"):
        raise ValueError(
            "external MCP Git artifact version does not match official pin"
        )


def _validate_launch_binding(
    argv: Sequence[str], artifact: ExternalMCPArtifactResolution | None
) -> None:
    if artifact is None:
        raise ValueError("external MCP launch artifact is required")
    executable = argv[0]
    basename = PurePosixPath(executable.replace("\\", "/")).name.lower()
    if basename in _IMPLICIT_INSTALLERS:
        raise ValueError("external MCP implicit installer commands are forbidden")
    if not PurePosixPath(executable).is_absolute():
        raise ValueError("external MCP executable path must be absolute")
    if artifact.version not in executable:
        raise ValueError("external MCP executable path must bind the exact version")


def _declared_secret_bindings(
    declarations: Any,
    bindings: Mapping[str, SecretReference],
    field_name: str,
) -> Mapping[str, SecretReference]:
    if not isinstance(declarations, (list, tuple)):
        raise ValueError(f"official MCP {field_name} declarations are invalid")
    declared: dict[str, Mapping[str, Any]] = {}
    for item in declarations:
        if not isinstance(item, Mapping):
            raise ValueError(f"official MCP {field_name} declaration is invalid")
        name = item.get("name")
        pattern = _HEADER_RE if field_name == "header" else _ENV_RE
        if not isinstance(name, str) or not pattern.fullmatch(name) or name in declared:
            raise ValueError(f"official MCP {field_name} declaration name is invalid")
        declared[name] = item
    if set(bindings) - set(declared):
        raise ValueError(f"external MCP {field_name} binding is undeclared")
    for name, item in declared.items():
        required = item.get("isRequired") is True
        secret = item.get("isSecret") is True
        default = item.get("default")
        if secret and default not in {None, "<redacted>"}:
            raise ValueError("official MCP metadata retained a secret value")
        if (required or secret) and name not in bindings:
            raise ValueError(f"external MCP {field_name} reference is required")
    return dict(sorted(bindings.items()))


def _target_configuration(
    descriptor: ExternalMCPDescriptor, target_id: str
) -> Mapping[str, Any]:
    env_names = tuple(reference.name for reference in descriptor.environment.values())
    header_names = tuple(
        (name, reference.name) for name, reference in descriptor.headers.items()
    )
    if target_id == HARNESS_MANAGED_MCP_TARGET_ID:
        return external_mcp_descriptor_to_dict(descriptor)
    if target_id == CODEX_MCP_TARGET_ID:
        server = CodexMCPServerSpec(
            name=descriptor.id,
            transport=(
                CodexMCPTransport.STDIO
                if descriptor.transport is MCPTransport.STDIO
                else CodexMCPTransport.STREAMABLE_HTTP
            ),
            command=descriptor.command,
            args=descriptor.args,
            cwd=descriptor.cwd,
            env_vars=env_names,
            url=descriptor.url,
            env_http_headers=header_names,
            startup_timeout_sec=descriptor.timeout_seconds,
            tool_timeout_sec=descriptor.timeout_seconds,
            enabled_tools=descriptor.tool_policy.include_tools,
            disabled_tools=descriptor.tool_policy.exclude_tools,
            default_tools_approval_mode=(
                CodexMCPDefaultApproval.APPROVE
                if descriptor.tool_policy.default is PolicyDecision.ALLOW
                else CodexMCPDefaultApproval.PROMPT
            ),
        )
        return _native_spec_to_dict(server)
    if target_id == CLAUDE_MCP_TARGET_ID:
        server = ClaudeMCPServerSpec(
            name=descriptor.id,
            transport=(
                ClaudeMCPTransport.STDIO
                if descriptor.transport is MCPTransport.STDIO
                else ClaudeMCPTransport.HTTP
            ),
            command=descriptor.command,
            args=descriptor.args,
            env_vars=env_names,
            url=descriptor.url,
            env_http_headers=header_names,
        )
        return _native_spec_to_dict(server)
    server = GeminiMCPServerSpec(
        name=descriptor.id,
        transport=(
            GeminiMCPTransport.STDIO
            if descriptor.transport is MCPTransport.STDIO
            else GeminiMCPTransport.SSE
            if descriptor.transport is MCPTransport.SSE
            else GeminiMCPTransport.HTTP
        ),
        command=descriptor.command,
        args=descriptor.args,
        cwd=descriptor.cwd,
        env_vars=env_names,
        url=descriptor.url,
        env_http_headers=header_names,
        timeout_ms=descriptor.timeout_seconds * 1000,
        description=descriptor.description,
        include_tools=descriptor.tool_policy.include_tools,
        exclude_tools=descriptor.tool_policy.exclude_tools,
    )
    return _native_spec_to_dict(server)


def _native_spec_to_dict(server: Any) -> dict[str, Any]:
    result = {
        "name": server.name,
        "transport": server.transport.value,
        "command": server.command,
        "args": list(server.args),
        "env_vars": list(server.env_vars),
        "url": server.url,
        "env_http_headers": [list(item) for item in server.env_http_headers],
        "enabled": server.enabled,
    }
    if hasattr(server, "cwd"):
        result["cwd"] = server.cwd
    for field_name in (
        "startup_timeout_sec",
        "tool_timeout_sec",
        "timeout_ms",
        "description",
        "enabled_tools",
        "disabled_tools",
        "include_tools",
        "exclude_tools",
        "default_tools_approval_mode",
    ):
        if hasattr(server, field_name):
            value = getattr(server, field_name)
            if isinstance(value, Enum):
                value = value.value
            elif isinstance(value, tuple):
                value = list(value)
            result[field_name] = value
    return result
