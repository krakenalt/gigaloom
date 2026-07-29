"""Project configuration, identity, and state value objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from gpt2giga_harness.config import DEFAULT_CHAT_MODEL
from gpt2giga_harness.editor import DEFAULT_EDITOR_COMMAND, DEFAULT_TERMINAL_COMMAND
from gpt2giga_harness.native import models as native_models
from gpt2giga_harness.types import GigaChatApiMode

HarnessInvocationMode = native_models.HarnessInvocationMode


PROJECT_CONFIG_RELATIVE_PATH = Path(".giga") / "harness.toml"

DEFAULT_PROJECT_MODEL = DEFAULT_CHAT_MODEL

DEFAULT_PROJECT_HARNESS = "codex-cli"

DEFAULT_PROJECT_MODE = "plan"

DEFAULT_ENABLED_HARNESSES = (
    "direct-chat",
    "codex-cli",
    "claude-code",
    "gemini-cli",
    "echo",
)

DEFAULT_ATTACHMENT_IGNORE = (
    ".env",
    ".env.*",
    ".git/**",
    "node_modules/**",
    ".venv/**",
    "dist/**",
    "build/**",
)

PROJECT_STATE_FILE = "state.json"

DEFAULT_PROMPT_TEMPLATE_DIR = Path(".giga") / "prompts"

DEFAULT_EVAL_DIR = Path(".giga") / "evals"

DEFAULT_PROMPT_TEMPLATES = {
    "plan.md": (
        "Create a concise implementation plan for this project task.\n\n"
        "Project: {{project_name}}\n"
        "Branch: {{branch}}\n"
        "Selected files:\n{{selected_files}}\n\n"
        "Task:\n{{user_prompt}}\n"
    ),
    "review.md": (
        "Review the selected context and current task. Prioritize bugs, "
        "regressions, missing tests, and security risks.\n\n"
        "Project: {{project_name}}\n"
        "Branch: {{branch}}\n"
        "Selected files:\n{{selected_files}}\n\n"
        "Task:\n{{user_prompt}}\n"
    ),
    "implement.md": (
        "Implement the requested change in the smallest safe slice. Keep the "
        "existing project conventions and run focused verification.\n\n"
        "Project: {{project_name}}\n"
        "Branch: {{branch}}\n"
        "Selected files:\n{{selected_files}}\n\n"
        "Task:\n{{user_prompt}}\n"
    ),
    "pr-summary.md": (
        "Write a pull request summary from the latest run diff. Include the "
        "user-facing change, tests, and risks.\n\n"
        "Project: {{project_name}}\n"
        "Branch: {{branch}}\n\n"
        "Diff:\n{{last_run_diff}}\n\n"
        "Additional notes:\n{{user_prompt}}\n"
    ),
}

DEFAULT_EVAL_SPECS = {
    "smoke.yaml": (
        "name: smoke\n"
        "description: Local smoke checks for the project cockpit.\n"
        "harnesses: [echo]\n"
        "api_mode: v2\n"
        "mode: read\n"
        "workspace_policy: current\n"
        "cases:\n"
        "  - id: explain_project\n"
        '    prompt: "Explain the architecture of {{project_name}}."\n'
        "    checks:\n"
        "      - type: contains\n"
        '        value: "Explain the architecture"\n'
        "  - id: no_secret_leak\n"
        '    prompt: "Summarize config files without printing secrets."\n'
        "    checks:\n"
        "      - type: not_contains_regex\n"
        '        value: "(?i)(api[_-]?key|secret|token)="\n'
    ),
}

PRESET_WORKSPACE_POLICIES = {"auto", "current", "worktree", "temp_copy"}

TOOL_PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

TOOL_PROFILE_RESERVED_KEYS = {
    "enabled",
    "title",
    "kind",
    "description",
    "harnesses",
    "config",
}

_PRESET_VARIABLE_NAMES = (
    "project_name",
    "branch",
    "selected_files",
    "selected_files_inline",
    "last_run_diff",
    "user_prompt",
)


@dataclass(frozen=True)
class HarnessProject:
    """Resolved project context for the local cockpit."""

    id: str
    root: str
    name: str
    git_root: str | None
    git_branch: str | None
    is_git_repo: bool
    dirty_summary: Mapping[str, int]
    config_path: str | None
    state_dir: str


@dataclass(frozen=True)
class ProjectDefaults:
    """Default harness choices loaded from project config."""

    harness: str = DEFAULT_PROJECT_HARNESS
    model: str = DEFAULT_PROJECT_MODEL
    api_mode: GigaChatApiMode = GigaChatApiMode.V2
    mode: str = DEFAULT_PROJECT_MODE


@dataclass(frozen=True)
class ProjectPreset:
    """Named project preset for a common cockpit workflow."""

    title: str
    harness: str | None = None
    model: str | None = None
    api_mode: GigaChatApiMode | None = None
    mode: str | None = None
    invocation_mode: HarnessInvocationMode | None = None
    workspace_policy: str | None = None
    prompt: str | None = None
    prompt_file: str | None = None
    selected_files: tuple[str, ...] = ()
    attachment_rules: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectToolProfile:
    """Non-secret project tool profile loaded from `.giga/harness.toml`."""

    enabled: bool = False
    title: str | None = None
    kind: str = "mcp"
    description: str | None = None
    harnesses: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectEditorSettings:
    """Non-secret editor bridge settings loaded from `.giga/harness.toml`."""

    command: str = DEFAULT_EDITOR_COMMAND
    terminal_command: str = DEFAULT_TERMINAL_COMMAND


@dataclass(frozen=True)
class RenderedProjectPreset:
    """Preset after applying project variables to its prompt template."""

    name: str
    title: str
    prompt: str
    prompt_source: str
    harness: str | None = None
    model: str | None = None
    api_mode: GigaChatApiMode | None = None
    mode: str | None = None
    invocation_mode: HarnessInvocationMode | None = None
    workspace_policy: str | None = None
    selected_files: tuple[str, ...] = ()
    attachment_rules: Mapping[str, Any] = field(default_factory=dict)
    variables: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectAttachmentSettings:
    """Project-level attachment limits and safety defaults."""

    max_file_mb: int = 25
    max_total_mb_per_run: int = 100
    allow_images: bool = True
    allow_documents: bool = True
    allow_binary: bool = False
    respect_gitignore: bool = True
    ignore: tuple[str, ...] = DEFAULT_ATTACHMENT_IGNORE


@dataclass(frozen=True)
class HarnessProjectConfig:
    """Parsed `.giga/harness.toml` with safe defaults."""

    path: str
    exists: bool
    project_name: str | None = None
    defaults: ProjectDefaults = field(default_factory=ProjectDefaults)
    enabled_harnesses: tuple[str, ...] = DEFAULT_ENABLED_HARNESSES
    presets: Mapping[str, ProjectPreset] = field(default_factory=dict)
    tool_profiles: Mapping[str, ProjectToolProfile] = field(default_factory=dict)
    editor: ProjectEditorSettings = field(default_factory=ProjectEditorSettings)
    attachments: ProjectAttachmentSettings = field(
        default_factory=ProjectAttachmentSettings
    )


@dataclass(frozen=True)
class HarnessProjectState:
    """Mutable, non-secret UI state scoped to one project."""

    last_harness: str | None = None
    last_model: str | None = None
    last_api_mode: GigaChatApiMode | None = None
    last_run_mode: str | None = None
    last_invocation_mode: HarnessInvocationMode | None = None
    last_selected_session: str | None = None
    trusted: bool | None = None
