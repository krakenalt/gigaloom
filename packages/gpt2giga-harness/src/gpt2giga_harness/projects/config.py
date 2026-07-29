"""Loading and validation for non-secret project configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from gpt2giga_harness.native import models as native_models
from gpt2giga_harness.types import (
    SECRET_ENV_NAMES,
    SECRET_KEY_PARTS,
    GigaChatApiMode,
    parse_api_mode,
)

from .models import (
    DEFAULT_ATTACHMENT_IGNORE,
    DEFAULT_EDITOR_COMMAND,
    DEFAULT_ENABLED_HARNESSES,
    DEFAULT_PROJECT_HARNESS,
    DEFAULT_PROJECT_MODE,
    DEFAULT_PROJECT_MODEL,
    DEFAULT_TERMINAL_COMMAND,
    HarnessInvocationMode,
    HarnessProjectConfig,
    ProjectAttachmentSettings,
    ProjectDefaults,
    ProjectEditorSettings,
    ProjectPreset,
    ProjectToolProfile,
    PRESET_WORKSPACE_POLICIES,
    PROJECT_CONFIG_RELATIVE_PATH,
    TOOL_PROFILE_NAME_PATTERN,
    TOOL_PROFILE_RESERVED_KEYS,
)

parse_invocation_mode = native_models.parse_invocation_mode


def project_config_path(project_root: str | Path) -> Path:
    """Return the expected project config path."""
    return Path(project_root).expanduser().resolve() / PROJECT_CONFIG_RELATIVE_PATH


def load_project_config(project_root: str | Path) -> HarnessProjectConfig:
    """Load `.giga/harness.toml`, returning defaults when it is absent."""
    path = project_config_path(project_root)
    if not path.exists():
        return HarnessProjectConfig(path=str(path), exists=False)
    data = _load_toml(path)
    _reject_secret_keys(data)
    project_data = _mapping(data.get("project"))
    defaults_data = _mapping(data.get("defaults"))
    harnesses_data = _mapping(data.get("harnesses"))
    editor_data = _mapping(data.get("editor"))
    attachments_data = _mapping(data.get("attachments"))
    tools_data = _mapping(data.get("tools"))
    return HarnessProjectConfig(
        path=str(path),
        exists=True,
        project_name=_optional_text(project_data.get("name")),
        defaults=_parse_defaults(defaults_data),
        enabled_harnesses=_string_tuple(
            harnesses_data.get("enabled"),
            default=DEFAULT_ENABLED_HARNESSES,
        ),
        presets=_parse_presets(_mapping(data.get("presets"))),
        tool_profiles=_parse_tool_profiles(tools_data),
        editor=_parse_editor_settings(editor_data),
        attachments=_parse_attachment_settings(attachments_data),
    )


def default_project_config_text(project_name: str) -> str:
    """Return the default `.giga/harness.toml` contents."""
    quoted_name = _toml_quote(project_name)
    ignored = "\n".join(
        f"  {_toml_quote(pattern)}," for pattern in DEFAULT_ATTACHMENT_IGNORE
    )
    enabled = ", ".join(_toml_quote(harness) for harness in DEFAULT_ENABLED_HARNESSES)
    return (
        "[project]\n"
        f"name = {quoted_name}\n"
        "\n"
        "[defaults]\n"
        f'harness = "{DEFAULT_PROJECT_HARNESS}"\n'
        f'model = "{DEFAULT_PROJECT_MODEL}"\n'
        'api_mode = "v2"\n'
        f'mode = "{DEFAULT_PROJECT_MODE}"\n'
        "\n"
        "[harnesses]\n"
        f"enabled = [{enabled}]\n"
        "\n"
        "[editor]\n"
        f'command = "{DEFAULT_EDITOR_COMMAND}"\n'
        f'terminal_command = "{DEFAULT_TERMINAL_COMMAND}"\n'
        "\n"
        "[presets.ask]\n"
        'title = "Ask"\n'
        'harness = "direct-chat"\n'
        'mode = "plan"\n'
        'api_mode = "v2"\n'
        'prompt = "{{user_prompt}}"\n'
        "\n"
        "[presets.plan]\n"
        'title = "Plan"\n'
        'harness = "codex-cli"\n'
        'mode = "plan"\n'
        'api_mode = "v2"\n'
        'workspace_policy = "current"\n'
        'prompt_file = ".giga/prompts/plan.md"\n'
        "\n"
        "[presets.review]\n"
        'title = "Review"\n'
        'harness = "claude-code"\n'
        'mode = "read"\n'
        'api_mode = "v2"\n'
        'workspace_policy = "current"\n'
        'prompt_file = ".giga/prompts/review.md"\n'
        "\n"
        "[presets.fix_tests]\n"
        'title = "Fix tests"\n'
        'harness = "codex-cli"\n'
        'mode = "edit"\n'
        'api_mode = "v2"\n'
        'workspace_policy = "worktree"\n'
        'prompt = "Run the relevant tests, diagnose failures, and propose the minimal patch. {{user_prompt}}"\n'
        "\n"
        "[presets.implement]\n"
        'title = "Implement"\n'
        'harness = "codex-cli"\n'
        'mode = "edit"\n'
        'api_mode = "v2"\n'
        'workspace_policy = "worktree"\n'
        'prompt_file = ".giga/prompts/implement.md"\n'
        "\n"
        "[presets.explain_screenshot]\n"
        'title = "Explain screenshot"\n'
        'harness = "direct-chat"\n'
        'mode = "read"\n'
        'api_mode = "v2"\n'
        'prompt = "Explain the attached screenshot in the context of {{project_name}}. {{user_prompt}}"\n'
        "\n"
        "[presets.pr_summary]\n"
        'title = "PR summary"\n'
        'harness = "direct-chat"\n'
        'mode = "read"\n'
        'api_mode = "v2"\n'
        'prompt_file = ".giga/prompts/pr-summary.md"\n'
        "\n"
        "[tools.github]\n"
        "enabled = false\n"
        'title = "GitHub"\n'
        'kind = "mcp"\n'
        'description = "Dry-run placeholder for a project GitHub tool profile."\n'
        'harnesses = ["codex-cli", "claude-code", "gemini-cli"]\n'
        "\n"
        "[tools.postgres]\n"
        "enabled = false\n"
        'title = "Postgres"\n'
        'kind = "mcp"\n'
        'description = "Dry-run placeholder for a project database tool profile."\n'
        'harnesses = ["codex-cli", "claude-code"]\n'
        "\n"
        "[attachments]\n"
        "max_file_mb = 25\n"
        "max_total_mb_per_run = 100\n"
        "allow_images = true\n"
        "allow_documents = true\n"
        "allow_binary = false\n"
        "respect_gitignore = true\n"
        "ignore = [\n"
        f"{ignored}\n"
        "]\n"
    )


def _load_toml(path: Path) -> Mapping[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli
        except ModuleNotFoundError:
            return _parse_basic_toml(path.read_text(encoding="utf-8"))
        with path.open("rb") as stream:
            return tomli.load(stream)
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _parse_defaults(data: Mapping[str, Any]) -> ProjectDefaults:
    return ProjectDefaults(
        harness=_optional_text(data.get("harness")) or DEFAULT_PROJECT_HARNESS,
        model=_optional_text(data.get("model")) or DEFAULT_PROJECT_MODEL,
        api_mode=parse_api_mode(data.get("api_mode")),
        mode=_optional_text(data.get("mode")) or DEFAULT_PROJECT_MODE,
    )


def _parse_presets(data: Mapping[str, Any]) -> Mapping[str, ProjectPreset]:
    presets: dict[str, ProjectPreset] = {}
    for name, value in data.items():
        preset_data = _mapping(value)
        title = _optional_text(preset_data.get("title")) or str(name)
        api_mode_value = preset_data.get("api_mode")
        presets[str(name)] = ProjectPreset(
            title=title,
            harness=_optional_text(preset_data.get("harness")),
            model=_optional_text(preset_data.get("model")),
            api_mode=parse_api_mode(api_mode_value) if api_mode_value else None,
            mode=_optional_text(preset_data.get("mode")),
            invocation_mode=_parse_optional_invocation_mode(
                preset_data.get("invocation_mode")
            ),
            workspace_policy=_parse_preset_workspace_policy(
                preset_data.get("workspace_policy")
            ),
            prompt=_optional_text(preset_data.get("prompt")),
            prompt_file=_optional_text(preset_data.get("prompt_file")),
            selected_files=_string_tuple(
                preset_data.get("selected_files"),
                default=(),
            ),
            attachment_rules=_mapping(preset_data.get("attachments")),
        )
    return presets


def _parse_tool_profiles(data: Mapping[str, Any]) -> Mapping[str, ProjectToolProfile]:
    profiles: dict[str, ProjectToolProfile] = {}
    for raw_name, value in data.items():
        name = str(raw_name).strip()
        if not name:
            continue
        if not TOOL_PROFILE_NAME_PATTERN.match(name):
            raise ValueError(
                "Tool profile names may only contain letters, numbers, dots, "
                "underscores, and hyphens"
            )
        profile_data = _mapping(value)
        nested_config = _mapping(profile_data.get("config"))
        inline_config = {
            str(key): item
            for key, item in profile_data.items()
            if str(key) not in TOOL_PROFILE_RESERVED_KEYS
        }
        config = {**inline_config, **dict(nested_config)}
        profiles[name] = ProjectToolProfile(
            enabled=_bool(profile_data.get("enabled"), False),
            title=_optional_text(profile_data.get("title")),
            kind=_optional_text(profile_data.get("kind")) or "mcp",
            description=_optional_text(profile_data.get("description")),
            harnesses=_string_tuple(profile_data.get("harnesses"), default=()),
            config=config,
        )
    return profiles


def _parse_editor_settings(data: Mapping[str, Any]) -> ProjectEditorSettings:
    return ProjectEditorSettings(
        command=_optional_text(data.get("command")) or DEFAULT_EDITOR_COMMAND,
        terminal_command=(
            _optional_text(data.get("terminal_command")) or DEFAULT_TERMINAL_COMMAND
        ),
    )


def _parse_preset_workspace_policy(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if text not in PRESET_WORKSPACE_POLICIES:
        raise ValueError(
            "Preset workspace_policy must be one of: "
            f"{', '.join(sorted(PRESET_WORKSPACE_POLICIES))}"
        )
    return text


def _parse_attachment_settings(
    data: Mapping[str, Any],
) -> ProjectAttachmentSettings:
    defaults = ProjectAttachmentSettings()
    return ProjectAttachmentSettings(
        max_file_mb=_positive_int(data.get("max_file_mb"), defaults.max_file_mb),
        max_total_mb_per_run=_positive_int(
            data.get("max_total_mb_per_run"),
            defaults.max_total_mb_per_run,
        ),
        allow_images=_bool(data.get("allow_images"), defaults.allow_images),
        allow_documents=_bool(data.get("allow_documents"), defaults.allow_documents),
        allow_binary=_bool(data.get("allow_binary"), defaults.allow_binary),
        respect_gitignore=_bool(
            data.get("respect_gitignore"),
            defaults.respect_gitignore,
        ),
        ignore=_string_tuple(data.get("ignore"), default=defaults.ignore),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return default
    items = tuple(str(item).strip() for item in value if str(item).strip())
    return items or default


def _parse_optional_api_mode(value: Any) -> GigaChatApiMode | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return parse_api_mode(text)
    except ValueError:
        return None


def _parse_optional_invocation_mode(value: Any) -> HarnessInvocationMode | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return parse_invocation_mode(text)
    except ValueError:
        return None


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _reject_secret_keys(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in _secret_env_names() or any(
                part in key_text for part in SECRET_KEY_PARTS
            ):
                dotted = ".".join((*path, str(key)))
                raise ValueError(
                    f"Project config must not contain secret key: {dotted}"
                )
            _reject_secret_keys(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, (*path, str(index)))


def _secret_env_names() -> set[str]:
    return {name.lower() for name in SECRET_ENV_NAMES}


def _toml_quote(value: str) -> str:
    return json.dumps(value)


def _parse_basic_toml(text: str) -> Mapping[str, Any]:
    data: dict[str, Any] = {}
    current = data
    pending_key: str | None = None
    pending_lines: list[str] = []
    pending_table: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if pending_key is not None:
            pending_lines.append(line)
            if line.endswith("]"):
                assert pending_table is not None
                pending_table[pending_key] = _parse_toml_value(" ".join(pending_lines))
                pending_key = None
                pending_lines = []
                pending_table = None
            continue
        if line.startswith("[") and line.endswith("]"):
            current = data
            for part in line[1:-1].split("."):
                current = current.setdefault(part.strip(), {})
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError("Invalid TOML line in project config")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and not value.endswith("]"):
            pending_key = key
            pending_lines = [value]
            pending_table = current
            continue
        current[key] = _parse_toml_value(value)
    if pending_key is not None:
        raise ValueError("Unterminated TOML array in project config")
    return data


def _strip_comment(line: str) -> str:
    in_string = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == "#" and not in_string:
            return line[:index]
    return line


def _parse_toml_value(value: str) -> Any:
    if value.startswith('"') and value.endswith('"'):
        return json.loads(value)
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_toml_value(item) for item in _split_toml_array(inner)]
    try:
        return int(value)
    except ValueError:
        return value


def _split_toml_array(value: str) -> list[str]:
    items: list[str] = []
    start = 0
    in_string = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == "," and not in_string:
            item = value[start:index].strip()
            if item:
                items.append(item)
            start = index + 1
    tail = value[start:].strip()
    if tail:
        items.append(tail)
    return items
