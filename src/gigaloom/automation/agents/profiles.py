"""Profiles for the agents subcontext."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
import yaml
from gigaloom.automation.agents.authoring import (
    ProjectAuthoringService,
    ProjectFileDraft,
)
from gigaloom.automation.ports import permission_profile
from gigaloom.safe_paths import resolve_operator_path, resolve_path_within
from gigaloom.types import parse_api_mode, redact_secrets
from .constants import (
    AGENT_DIRECTORY as AGENT_DIRECTORY,
    AGENT_ID_PATTERN as AGENT_ID_PATTERN,
    AGENT_SCHEMA_VERSION as AGENT_SCHEMA_VERSION,
    ALLOWED_MODES as ALLOWED_MODES,
    ALLOWED_WORKSPACE_POLICIES as ALLOWED_WORKSPACE_POLICIES,
    NON_SECRET_PROFILE_KEYS as NON_SECRET_PROFILE_KEYS,
    SECRET_KEY_PARTS as SECRET_KEY_PARTS,
    STARTER_AGENT_PROFILES as STARTER_AGENT_PROFILES,
)
from .models import (
    AgentBudgets as AgentBudgets,
    AgentProfile as AgentProfile,
    AgentProfileLoadError as AgentProfileLoadError,
)


def parse_agent_profile(
    content: str, *, source_path: str | None = None
) -> AgentProfile:
    """Parse one strict, secret-free AgentProfile YAML document."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid agent profile YAML") from exc
    if not isinstance(data, Mapping):
        raise ValueError("Agent profile must be a YAML mapping")
    _reject_secret_literals(data)
    allowed = {
        "id",
        "title",
        "description",
        "schema_version",
        "harness_id",
        "instructions",
        "model",
        "reasoning_effort",
        "api_mode",
        "invocation_mode",
        "mode",
        "workspace_policy",
        "permission_profile",
        "prompt_files",
        "skills",
        "memory_selectors",
        "context_selectors",
        "tool_ids",
        "allowed_tools",
        "disallowed_tools",
        "budgets",
        "expected_artifact",
        "provenance",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"Unknown agent profile fields: {', '.join(unknown)}")
    agent_id = _required_text(data.get("id"), "id")
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise ValueError("Agent id must match ^[a-z][a-z0-9_-]{1,63}$")
    mode = str(data.get("mode") or "plan")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unsupported agent mode: {mode}")
    workspace_policy = str(data.get("workspace_policy") or "auto")
    if workspace_policy not in ALLOWED_WORKSPACE_POLICIES:
        raise ValueError(f"Unsupported workspace policy: {workspace_policy}")
    api_mode = parse_api_mode(data.get("api_mode") or "v2").value
    invocation_mode = str(data.get("invocation_mode") or "headless")
    if invocation_mode not in {"headless", "native"}:
        raise ValueError(f"Unsupported invocation mode: {invocation_mode}")
    selected_permission = permission_profile(
        data.get("permission_profile") or "interactive"
    )
    budget_data = data.get("budgets") or {}
    if not isinstance(budget_data, Mapping):
        raise ValueError("Agent budgets must be a mapping")
    budgets = AgentBudgets(
        timeout_seconds=_optional_positive_int(
            budget_data.get("timeout_seconds"), "timeout_seconds"
        ),
        max_tokens=_optional_positive_int(budget_data.get("max_tokens"), "max_tokens"),
        max_attempts=_positive_int(budget_data.get("max_attempts", 1), "max_attempts"),
        max_concurrency=_positive_int(
            budget_data.get("max_concurrency", 1), "max_concurrency"
        ),
    )
    reasoning_effort = _optional_text(data.get("reasoning_effort"))
    if reasoning_effort not in {None, "none", "low", "medium", "high"}:
        raise ValueError("Unsupported reasoning_effort")
    return AgentProfile(
        id=agent_id,
        title=_required_text(data.get("title"), "title"),
        description=str(data.get("description") or "").strip(),
        schema_version=_supported_schema_version(data.get("schema_version", 1)),
        harness_id=_required_text(data.get("harness_id"), "harness_id"),
        instructions=_required_text(data.get("instructions"), "instructions"),
        model=_optional_text(data.get("model")),
        reasoning_effort=reasoning_effort,
        api_mode=api_mode,
        invocation_mode=invocation_mode,
        mode=mode,
        workspace_policy=workspace_policy,
        permission_profile=selected_permission.id,
        prompt_files=_safe_paths(data.get("prompt_files"), "prompt_files"),
        skills=_text_tuple(data.get("skills"), "skills"),
        memory_selectors=_text_tuple(data.get("memory_selectors"), "memory_selectors"),
        context_selectors=_safe_paths(
            data.get("context_selectors"), "context_selectors"
        ),
        tool_ids=_text_tuple(data.get("tool_ids"), "tool_ids"),
        allowed_tools=_tool_selectors(data.get("allowed_tools"), "allowed_tools"),
        disallowed_tools=_tool_selectors(
            data.get("disallowed_tools"), "disallowed_tools"
        ),
        budgets=budgets,
        expected_artifact=_optional_text(data.get("expected_artifact")),
        provenance=dict(_profile_mapping(data.get("provenance"))),
        source_path=source_path,
        source_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def discover_agent_profiles(
    project_root: str | Path,
) -> tuple[tuple[AgentProfile, ...], tuple[AgentProfileLoadError, ...]]:
    """Load all project profiles while reporting invalid files independently."""
    root = resolve_operator_path(project_root)
    directory = root / AGENT_DIRECTORY
    profiles: list[AgentProfile] = []
    errors: list[AgentProfileLoadError] = []
    for path in sorted((*directory.glob("*.yaml"), *directory.glob("*.yml"))):
        relative = path.relative_to(root).as_posix()
        try:
            profile = parse_agent_profile(
                path.read_text(encoding="utf-8"), source_path=relative
            )
            if path.stem != profile.id:
                raise ValueError("Agent filename must match its id")
            profiles.append(profile)
        except (OSError, ValueError) as exc:
            errors.append(AgentProfileLoadError(path=relative, error=str(exc)))
    return tuple(profiles), tuple(errors)


def load_agent_profile(project_root: str | Path, agent_id: str) -> AgentProfile:
    """Load one profile by safe id."""
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise KeyError(agent_id)
    root = resolve_operator_path(project_root)
    path = resolve_path_within(root, AGENT_DIRECTORY / f"{agent_id}.yaml")
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise KeyError(agent_id) from exc
    profile = parse_agent_profile(
        content, source_path=path.relative_to(root).as_posix()
    )
    if profile.id != agent_id:
        raise ValueError("Agent filename must match its id")
    return profile


def draft_agent_profile(
    project_root: str | Path,
    agent_id: str,
    content: str,
    *,
    expected_hash: str | None = None,
) -> ProjectFileDraft[AgentProfile]:
    """Validate and preview an agent profile through the shared authoring service."""
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise ValueError("Invalid agent id")
    relative = AGENT_DIRECTORY / f"{agent_id}.yaml"
    service = ProjectAuthoringService(project_root)
    draft = service.draft(
        relative,
        content,
        validate=lambda value: parse_agent_profile(
            value, source_path=relative.as_posix()
        ),
        expected_hash=expected_hash,
    )
    if draft.value.id != agent_id:
        raise ValueError("Agent filename must match its id")
    return draft


def agent_profile_to_dict(profile: AgentProfile) -> dict[str, Any]:
    """Serialize a profile or immutable run snapshot."""
    payload = asdict(profile)
    redacted = dict(redact_secrets(payload))
    budgets = redacted.get("budgets")
    if isinstance(budgets, Mapping):
        redacted["budgets"] = {
            **dict(budgets),
            "max_tokens": profile.budgets.max_tokens,
        }
    return redacted


def render_starter_agent(agent_id: str, *, harness_id: str = "codex-cli") -> str:
    """Render one deterministic starter AgentProfile YAML document."""
    item = STARTER_AGENT_PROFILES[agent_id]
    payload = {
        "id": agent_id,
        "title": item["title"],
        "description": f"Starter {item['title']} profile.",
        "schema_version": AGENT_SCHEMA_VERSION,
        "harness_id": harness_id,
        "instructions": item["instructions"],
        "api_mode": "v2",
        "invocation_mode": "headless",
        "mode": item["mode"],
        "workspace_policy": item.get("workspace_policy", "auto"),
        "permission_profile": "interactive",
        "tool_ids": [],
        "allowed_tools": [],
        "disallowed_tools": [],
        "budgets": {"max_attempts": 1, "max_concurrency": 1},
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _reject_secret_literals(value: Any, path: str = "profile") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower().replace("-", "_")
            if (
                name not in NON_SECRET_PROFILE_KEYS
                and any(part in name for part in SECRET_KEY_PARTS)
                and item
                not in (
                    None,
                    "",
                    [],
                )
            ):
                raise ValueError(
                    f"Secret literals are not allowed in agent profiles: {path}.{key}"
                )
            _reject_secret_literals(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_literals(item, f"{path}[{index}]")
    elif isinstance(value, str) and redact_secrets(value) != value:
        raise ValueError(
            f"Secret-looking values are not allowed in agent profiles: {path}"
        )


def _safe_paths(value: Any, field_name: str) -> tuple[str, ...]:
    paths = _text_tuple(value, field_name)
    for item in paths:
        path = PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts or item.startswith("~"):
            raise ValueError(f"Unsafe path in {field_name}: {item}")
    return paths


def _text_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _tool_selectors(value: Any, field_name: str) -> tuple[str, ...]:
    selectors = _text_tuple(value, field_name)
    for selector in selectors:
        if (
            len(selector) > 200
            or selector.startswith("-")
            or "\x00" in selector
            or "\n" in selector
            or "\r" in selector
        ):
            raise ValueError(f"Unsafe tool selector in {field_name}: {selector!r}")
    return selectors


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Agent {field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _profile_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Agent provenance must be a mapping")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if number < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return number


def _supported_schema_version(value: Any) -> int:
    version = _positive_int(value, "schema_version")
    if version != AGENT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported agent schema_version: {version}")
    return version


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    return None if value is None else _positive_int(value, field_name)
