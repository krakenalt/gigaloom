"""Public project configuration, bootstrap, memory, and state boundary."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .project_starter import init_project_config
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
    from .backup import (
        BACKUP_KIND,
        BACKUP_MANIFEST,
        BACKUP_SCHEMA_VERSION,
        LEGACY_BACKUP_SCHEMA_VERSION,
        MINIMUM_READER_SCHEMA_VERSION,
        STATE_LAYOUT_VERSION,
        StateBackupResult,
        StateRestoreResult,
        create_state_backup,
        restore_state_backup,
        verify_state_backup,
    )
    from .bootstrap import (
        BOOTSTRAP_APPLICATION_KIND,
        BOOTSTRAP_PREVIEW_KIND,
        BOOTSTRAP_SCHEMA_VERSION,
        BOOTSTRAP_STEP_IDS,
        BOOTSTRAP_STEP_MANAGED_STATE,
        BOOTSTRAP_STEP_PROJECT,
        MANAGED_STATE_DIRECTORIES,
        MAX_BOOTSTRAP_FILE_BYTES,
        BootstrapConflictError,
        BootstrapError,
        BootstrapNotFoundError,
        BootstrapService,
    )
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

_LAZY_EXPORT_MODULES = {
    **dict.fromkeys(
        {
            "BACKUP_KIND",
            "BACKUP_MANIFEST",
            "BACKUP_SCHEMA_VERSION",
            "LEGACY_BACKUP_SCHEMA_VERSION",
            "MINIMUM_READER_SCHEMA_VERSION",
            "STATE_LAYOUT_VERSION",
            "StateBackupResult",
            "StateRestoreResult",
            "create_state_backup",
            "restore_state_backup",
            "verify_state_backup",
        },
        "backup",
    ),
    **dict.fromkeys(
        {
            "BOOTSTRAP_APPLICATION_KIND",
            "BOOTSTRAP_PREVIEW_KIND",
            "BOOTSTRAP_SCHEMA_VERSION",
            "BOOTSTRAP_STEP_IDS",
            "BOOTSTRAP_STEP_MANAGED_STATE",
            "BOOTSTRAP_STEP_PROJECT",
            "MANAGED_STATE_DIRECTORIES",
            "MAX_BOOTSTRAP_FILE_BYTES",
            "BootstrapConflictError",
            "BootstrapError",
            "BootstrapNotFoundError",
            "BootstrapService",
        },
        "bootstrap",
    ),
    **dict.fromkeys(
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
        },
        "memory",
    ),
}


def __getattr__(name: str) -> Any:
    """Load optional project services only when their public symbol is requested."""
    module_name = _LAZY_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(f".{module_name}", __package__)
    return getattr(module, name)


def __dir__() -> list[str]:
    """Include lazy project-service exports in module discovery."""
    return sorted({*globals(), *_LAZY_EXPORT_MODULES})


__all__ = [
    "BACKUP_KIND",
    "BACKUP_MANIFEST",
    "BACKUP_SCHEMA_VERSION",
    "BOOTSTRAP_APPLICATION_KIND",
    "BOOTSTRAP_PREVIEW_KIND",
    "BOOTSTRAP_SCHEMA_VERSION",
    "BOOTSTRAP_STEP_IDS",
    "BOOTSTRAP_STEP_MANAGED_STATE",
    "BOOTSTRAP_STEP_PROJECT",
    "BootstrapConflictError",
    "BootstrapError",
    "BootstrapNotFoundError",
    "BootstrapService",
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
    "LEGACY_BACKUP_SCHEMA_VERSION",
    "MANAGED_STATE_DIRECTORIES",
    "MAX_INCLUDED_MEMORY",
    "MAX_MEMORY_TAGS",
    "MAX_MEMORY_TEXT_CHARS",
    "MAX_BOOTSTRAP_FILE_BYTES",
    "MINIMUM_READER_SCHEMA_VERSION",
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
    "STATE_LAYOUT_VERSION",
    "StateBackupResult",
    "StateRestoreResult",
    "TOOL_PROFILE_NAME_PATTERN",
    "TOOL_PROFILE_RESERVED_KEYS",
    "default_project_config_text",
    "create_state_backup",
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
    "restore_state_backup",
    "resolve_project",
    "save_project_state",
    "update_project_state",
    "utc_now",
    "verify_state_backup",
]
