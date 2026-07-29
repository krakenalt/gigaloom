"""Public project configuration, memory, and state boundary."""

from typing import TYPE_CHECKING, Any

from .bootstrap import init_project_config
from .config import (
    default_project_config_text,
    load_project_config,
    project_config_path,
)
from .models import (
    DEFAULT_ATTACHMENT_IGNORE,
    DEFAULT_ENABLED_HARNESSES,
    DEFAULT_EVAL_DIR,
    DEFAULT_EVAL_SPECS,
    DEFAULT_PROJECT_HARNESS,
    DEFAULT_PROJECT_MODE,
    DEFAULT_PROJECT_MODEL,
    DEFAULT_PROMPT_TEMPLATE_DIR,
    DEFAULT_PROMPT_TEMPLATES,
    PRESET_WORKSPACE_POLICIES,
    PROJECT_CONFIG_RELATIVE_PATH,
    PROJECT_STATE_FILE,
    TOOL_PROFILE_NAME_PATTERN,
    TOOL_PROFILE_RESERVED_KEYS,
    HarnessProject,
    HarnessProjectConfig,
    HarnessProjectState,
    ProjectAttachmentSettings,
    ProjectDefaults,
    ProjectEditorSettings,
    ProjectPreset,
    ProjectToolProfile,
    RenderedProjectPreset,
)
from .preferences import (
    load_project_state,
    project_state_from_dict,
    project_state_path,
    project_state_to_dict,
    save_project_state,
    update_project_state,
)
from .presets import render_project_preset
from .resolution import project_id_for_root, resolve_project
from .serialization import (
    project_config_to_dict,
    project_preset_to_dict,
    project_to_dict,
    project_tool_profile_to_dict,
    rendered_project_preset_to_dict,
)

if TYPE_CHECKING:
    from .memory import (
        MAX_INCLUDED_MEMORY,
        MAX_MEMORY_TAGS,
        MAX_MEMORY_TEXT_CHARS,
        PROJECT_MEMORY_FILE,
        FilesystemProjectMemoryStore,
        ProjectMemoryEntry,
        ProjectMemoryNotFoundError,
        memory_entries_to_context,
        memory_entries_to_prompt,
        memory_entry_from_dict,
        memory_entry_to_dict,
        new_memory_id,
        utc_now,
    )

_MEMORY_EXPORTS = frozenset(
    {
        "MAX_INCLUDED_MEMORY",
        "MAX_MEMORY_TAGS",
        "MAX_MEMORY_TEXT_CHARS",
        "PROJECT_MEMORY_FILE",
        "FilesystemProjectMemoryStore",
        "ProjectMemoryEntry",
        "ProjectMemoryNotFoundError",
        "memory_entries_to_context",
        "memory_entries_to_prompt",
        "memory_entry_from_dict",
        "memory_entry_to_dict",
        "new_memory_id",
        "utc_now",
    }
)


def __getattr__(name: str) -> Any:
    """Load project memory only when a public memory symbol is requested."""
    if name not in _MEMORY_EXPORTS:
        raise AttributeError(name)
    from . import memory

    return getattr(memory, name)


def __dir__() -> list[str]:
    """Include lazy memory exports in module discovery."""
    return sorted({*globals(), *_MEMORY_EXPORTS})


__all__ = [
    "DEFAULT_ATTACHMENT_IGNORE",
    "DEFAULT_ENABLED_HARNESSES",
    "DEFAULT_EVAL_DIR",
    "DEFAULT_EVAL_SPECS",
    "DEFAULT_PROJECT_HARNESS",
    "DEFAULT_PROJECT_MODE",
    "DEFAULT_PROJECT_MODEL",
    "DEFAULT_PROMPT_TEMPLATE_DIR",
    "DEFAULT_PROMPT_TEMPLATES",
    "FilesystemProjectMemoryStore",
    "HarnessProject",
    "HarnessProjectConfig",
    "HarnessProjectState",
    "MAX_INCLUDED_MEMORY",
    "MAX_MEMORY_TAGS",
    "MAX_MEMORY_TEXT_CHARS",
    "PRESET_WORKSPACE_POLICIES",
    "PROJECT_CONFIG_RELATIVE_PATH",
    "PROJECT_MEMORY_FILE",
    "PROJECT_STATE_FILE",
    "ProjectAttachmentSettings",
    "ProjectDefaults",
    "ProjectEditorSettings",
    "ProjectMemoryEntry",
    "ProjectMemoryNotFoundError",
    "ProjectPreset",
    "ProjectToolProfile",
    "RenderedProjectPreset",
    "TOOL_PROFILE_NAME_PATTERN",
    "TOOL_PROFILE_RESERVED_KEYS",
    "default_project_config_text",
    "init_project_config",
    "load_project_config",
    "load_project_state",
    "memory_entries_to_context",
    "memory_entries_to_prompt",
    "memory_entry_from_dict",
    "memory_entry_to_dict",
    "new_memory_id",
    "project_config_path",
    "project_config_to_dict",
    "project_id_for_root",
    "project_preset_to_dict",
    "project_state_from_dict",
    "project_state_path",
    "project_state_to_dict",
    "project_to_dict",
    "project_tool_profile_to_dict",
    "render_project_preset",
    "rendered_project_preset_to_dict",
    "resolve_project",
    "save_project_state",
    "update_project_state",
    "utc_now",
]
