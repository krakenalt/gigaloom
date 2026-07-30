"""Project-profile normalization into MCP inventory contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from gigaloom.project import (
    HarnessProject,
    ProjectToolProfile,
    load_project_state,
)
from gigaloom.secrets import (
    SecretReference,
    secret_reference_from_dict,
)
from gigaloom.tools import PolicyDecision, ToolExecutionPolicy, ToolRisk

from .contracts import MCPTransport, ToolServerDescriptor


def descriptor_from_profile(
    name: str, profile: ProjectToolProfile
) -> ToolServerDescriptor:
    """Parse one non-secret project profile into a strict MCP descriptor."""
    if profile.kind.lower() != "mcp":
        raise ValueError("tool profile kind must be mcp")
    config = profile.config
    transport = MCPTransport(str(config.get("transport") or "stdio"))
    policy_id = str(config.get("policy_id") or "default")
    risk_rules = _risk_rules(config.get("risk_policy"))
    tool_rules = _tool_rules(config.get("tool_policy"))
    return ToolServerDescriptor(
        id=name,
        title=profile.title or name,
        description=profile.description or "",
        transport=transport,
        command=_optional_text(config.get("command")),
        args=_string_tuple(config.get("args")),
        cwd=_optional_text(config.get("cwd")),
        url=_optional_text(config.get("url")),
        environment=_reference_mapping(config.get("env"), reject_secret_literals=True),
        headers=_reference_mapping(config.get("headers"), reject_secret_literals=True),
        instructions=_optional_text(config.get("instructions")) or "",
        trusted=bool(config.get("trusted", False)),
        enabled=profile.enabled,
        timeout_seconds=float(config.get("timeout_seconds") or 10.0),
        harnesses=profile.harnesses,
        execution_policy=ToolExecutionPolicy(
            id=policy_id,
            tool_rules=tool_rules,
            risk_rules=risk_rules,
        ),
    )


def build_mcp_inventory(
    profiles: Mapping[str, ProjectToolProfile],
    *,
    project: HarnessProject | None = None,
) -> tuple[tuple[ToolServerDescriptor, ...], tuple[dict[str, str], ...]]:
    """Build valid descriptors while returning safe per-profile errors."""
    project_trusted = (
        load_project_state(project).trusted is True if project is not None else False
    )
    descriptors: list[ToolServerDescriptor] = []
    errors: list[dict[str, str]] = []
    for name, profile in profiles.items():
        if profile.kind.lower() != "mcp":
            continue
        try:
            descriptor = descriptor_from_profile(name, profile)
            descriptors.append(
                replace(
                    descriptor,
                    trusted=descriptor.trusted and project_trusted,
                )
            )
        except (TypeError, ValueError) as exc:
            errors.append({"server_id": name, "error": str(exc)})
    return tuple(descriptors), tuple(errors)


def _reference_mapping(
    value: Any, *, reject_secret_literals: bool = False
) -> dict[str, str | SecretReference]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("MCP env/headers must be a mapping")
    result: dict[str, str | SecretReference] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError("MCP env/header names must not be empty")
        if isinstance(raw_value, Mapping):
            reference_data = raw_value.get("secret_ref", raw_value)
            if not isinstance(reference_data, Mapping):
                raise ValueError(f"invalid secret reference for {key}")
            result[key] = secret_reference_from_dict(reference_data)
        elif isinstance(raw_value, str):
            if reject_secret_literals and _is_sensitive_name(key):
                raise ValueError(f"sensitive value {key} must use secret_ref")
            result[key] = raw_value
        else:
            raise ValueError(
                f"MCP env/header value for {key} must be text or secret_ref"
            )
    return result


def _risk_rules(value: Any) -> dict[ToolRisk, PolicyDecision]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("risk_policy must be a mapping")
    return {
        ToolRisk(str(key)): PolicyDecision(str(item)) for key, item in value.items()
    }


def _tool_rules(value: Any) -> dict[str, PolicyDecision]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("tool_policy must be a mapping")
    return {str(key): PolicyDecision(str(item)) for key, item in value.items()}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("MCP args must be a list")
    return tuple(str(item) for item in value)


def _is_sensitive_name(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    markers = (
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
        "api_key",
        "cookie",
    )
    return any(
        normalized == marker
        or normalized.startswith(f"{marker}_")
        or normalized.endswith(f"_{marker}")
        for marker in markers
    )
