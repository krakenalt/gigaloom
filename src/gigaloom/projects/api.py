"""Public project configuration, workspace, and environment boundary."""

# ruff: noqa: F401

from importlib import import_module
from typing import TYPE_CHECKING, Any

from . import instructions_api
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
from .catalog.codec import (
    catalog_entry_digest,
    catalog_entry_from_dict,
    catalog_entry_to_dict,
)
from .catalog.errors import (
    ProjectCatalogCapacityError,
    ProjectCatalogConflictError,
    ProjectCatalogError,
    ProjectCatalogNotFoundError,
)
from .catalog.models import (
    MAX_CATALOG_ENTRIES,
    MAX_CATALOG_PAGE_SIZE,
    PROJECT_CATALOG_SCHEMA_VERSION,
    ProjectCatalogEntryV1,
    ProjectCatalogPageV1,
    ProjectLocationRef,
    ProjectRelocationPreviewV1,
)
from .catalog.migration import (
    MAX_MIGRATION_SESSIONS,
    PROJECT_CATALOG_MIGRATION_ID,
    PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION,
    InjectedProjectCatalogMigrationCrash,
    ProjectCatalogMigrationReceiptV1,
    ProjectCatalogMigrationService,
)
from .catalog.repository import FilesystemProjectCatalogRepository
from .catalog.session_bindings import SessionCatalogBindingService
from .catalog.service import ProjectCatalogService, resolved_project_location
from .launch_profiles.codec import (
    launch_profile_digest,
    launch_profile_from_dict,
    launch_profile_to_dict,
)
from .launch_profiles.models import (
    MAX_LAUNCH_PROFILES,
    MAX_LAUNCH_PROFILE_PAGE_SIZE,
    PROJECT_LAUNCH_PROFILE_SCHEMA_VERSION,
    LaunchProfilePageV1,
    LaunchResolutionContextV1,
    ProjectLaunchProfileV1,
    ResolvedProjectLaunchProfileV1,
    TerminalModeHint,
    UnsatisfiedLaunchHintV1,
)
from .launch_profiles.repository import FilesystemLaunchProfileRepository
from .launch_profiles.resolution import resolve_launch_profile
from .launch_profiles.service import ProjectLaunchProfileService

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
    from .impact import (
        DEFAULT_MAX_PYTHON_FILE_BYTES,
        DEFAULT_MAX_PYTHON_FILES,
        ImpactUncertainty,
        ImpactUncertaintyKind,
        ImpactedPythonFile,
        PublicContractMarker,
        PythonImpactIndex,
        PythonImpactResult,
        analyze_python_impact,
        compile_python_impact_index,
        project_python_impact,
    )
    from .impact_cache import (
        PythonImpactIndexCache,
        StalePythonImpactIndexError,
    )
    from .state_migration import (
        CANONICAL_STATE_RELATIVE_PATH,
        LEGACY_STATE_RELATIVE_PATH,
        MIGRATION_SUPPORT_RELATIVE_PATH,
        STATE_MIGRATION_ID,
        STATE_MIGRATION_SCHEMA_VERSION,
        InjectedStateMigrationCrash,
        StateMigrationResult,
        migrate_legacy_state,
        prepare_runtime_state,
        reject_legacy_state_override,
        rollback_legacy_state,
    )
    from .environment.commit import (
        EnvironmentCommitError,
        EnvironmentCommitOutcome,
        EnvironmentCommitPreview,
        EnvironmentCommitResult,
        EnvironmentCommitService,
        GovernedEnvironmentCommitService,
    )
    from .environment.editor import EditorOpenError, EditorOpenPlan
    from .environment.github import (
        GitHubEnvironmentService,
        GitHubEnvironmentSnapshot,
    )
    from .environment.models import (
        EnvironmentCaptureError,
        EnvironmentSnapshot,
        HostedRepositoryHint,
    )
    from .environment.pull_request import (
        EnvironmentPullRequestError,
        EnvironmentPullRequestOutcome,
        EnvironmentPullRequestPreview,
        EnvironmentPullRequestResult,
        EnvironmentPullRequestService,
        GovernedEnvironmentPullRequestService,
    )
    from .environment.push import (
        EnvironmentPushError,
        EnvironmentPushOutcome,
        EnvironmentPushPreview,
        EnvironmentPushResult,
        EnvironmentPushService,
        GovernedEnvironmentPushService,
    )
    from .environment.registry import EnvironmentProviderRegistry
    from .workspace.api import (
        MAX_PATCH_CHARS,
        RunDiffReview,
        WorkspaceDiff,
        WorkspaceExecution,
        WorkspacePolicy,
        WorktreeConflictError,
        WorktreeError,
        apply_run_diff,
        capture_workspace_diff,
        detect_overlapping_run_diffs,
        discard_run_worktree,
        open_worktree_response,
        parse_workspace_policy,
        prepare_run_diff_merge,
        prepare_workspace_execution,
        resolve_workspace,
        review_run_diff,
        run_diff_response,
        shlex_quote,
        workspace_file_metadata,
        workspace_tree,
    )

_LAZY_EXPORT_MODULES = {
    **dict.fromkeys(
        "MIGRATION_REGISTRY_SCHEMA_VERSION NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1 MigrationRegistrationV1 validate_migration_sequence PROJECT_LAUNCH_READ_MODEL_SCHEMA_VERSION LaunchProfileReadPort ProjectCatalogReadPort ProjectLaunchReadModelV1 ProjectLaunchReadService InjectedNativeAgentGatewayMigrationCrash NATIVE_AGENT_GATEWAY_MIGRATION_ID NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION NativeAgentGatewayMigrationReceiptV1 NativeAgentGatewayMigrationService TEXTUAL_PREFERENCES_RETIREMENT_ID".split(),
        "integration",
    ),
    **dict.fromkeys(
        {
            "PythonImpactIndexCache",
            "StalePythonImpactIndexError",
        },
        "impact_cache",
    ),
    **dict.fromkeys(
        {
            "DEFAULT_MAX_PYTHON_FILE_BYTES",
            "DEFAULT_MAX_PYTHON_FILES",
            "ImpactUncertainty",
            "ImpactUncertaintyKind",
            "ImpactedPythonFile",
            "PublicContractMarker",
            "PythonImpactIndex",
            "PythonImpactResult",
            "analyze_python_impact",
            "compile_python_impact_index",
            "project_python_impact",
        },
        "impact",
    ),
    **dict.fromkeys(
        {
            "CANONICAL_STATE_RELATIVE_PATH",
            "InjectedStateMigrationCrash",
            "LEGACY_STATE_RELATIVE_PATH",
            "MIGRATION_SUPPORT_RELATIVE_PATH",
            "STATE_MIGRATION_ID",
            "STATE_MIGRATION_SCHEMA_VERSION",
            "StateMigrationResult",
            "migrate_legacy_state",
            "prepare_runtime_state",
            "reject_legacy_state_override",
            "rollback_legacy_state",
        },
        "state_migration",
    ),
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
    **dict.fromkeys(
        {
            "EnvironmentCaptureError",
            "EnvironmentSnapshot",
            "HostedRepositoryHint",
        },
        "environment.models",
    ),
    **dict.fromkeys(
        {
            "EnvironmentProviderRegistry",
        },
        "environment.registry",
    ),
    **dict.fromkeys(
        {
            "EnvironmentCommitError",
            "EnvironmentCommitOutcome",
            "EnvironmentCommitPreview",
            "EnvironmentCommitResult",
            "EnvironmentCommitService",
            "GovernedEnvironmentCommitService",
        },
        "environment.commit",
    ),
    **dict.fromkeys(
        {
            "EnvironmentPushError",
            "EnvironmentPushOutcome",
            "EnvironmentPushPreview",
            "EnvironmentPushResult",
            "EnvironmentPushService",
            "GovernedEnvironmentPushService",
        },
        "environment.push",
    ),
    **dict.fromkeys(
        {
            "EnvironmentPullRequestError",
            "EnvironmentPullRequestOutcome",
            "EnvironmentPullRequestPreview",
            "EnvironmentPullRequestResult",
            "EnvironmentPullRequestService",
            "GovernedEnvironmentPullRequestService",
        },
        "environment.pull_request",
    ),
    **dict.fromkeys(
        {
            "GitHubEnvironmentService",
            "GitHubEnvironmentSnapshot",
        },
        "environment.github",
    ),
    **dict.fromkeys(
        {
            "EditorOpenError",
            "EditorOpenPlan",
        },
        "environment.editor",
    ),
    **dict.fromkeys(
        {
            "MAX_PATCH_CHARS",
            "RunDiffReview",
            "WorkspaceDiff",
            "WorkspaceExecution",
            "WorkspacePolicy",
            "WorktreeConflictError",
            "WorktreeError",
            "apply_run_diff",
            "capture_workspace_diff",
            "detect_overlapping_run_diffs",
            "discard_run_worktree",
            "open_worktree_response",
            "parse_workspace_policy",
            "prepare_run_diff_merge",
            "prepare_workspace_execution",
            "resolve_workspace",
            "review_run_diff",
            "run_diff_response",
            "shlex_quote",
            "workspace_file_metadata",
            "workspace_tree",
        },
        "workspace.api",
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
    "CANONICAL_STATE_RELATIVE_PATH",
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
    "DEFAULT_MAX_PYTHON_FILE_BYTES",
    "DEFAULT_MAX_PYTHON_FILES",
    "DEFAULT_PROMPT_TEMPLATE_DIR",
    "DEFAULT_PROMPT_TEMPLATES",
    "FilesystemProjectMemoryStore",
    "EditorOpenError",
    "EditorOpenPlan",
    "EnvironmentCaptureError",
    "EnvironmentCommitError",
    "EnvironmentCommitOutcome",
    "EnvironmentCommitPreview",
    "EnvironmentCommitResult",
    "EnvironmentCommitService",
    "EnvironmentProviderRegistry",
    "EnvironmentPullRequestError",
    "EnvironmentPullRequestOutcome",
    "EnvironmentPullRequestPreview",
    "EnvironmentPullRequestResult",
    "EnvironmentPullRequestService",
    "EnvironmentPushError",
    "EnvironmentPushOutcome",
    "EnvironmentPushPreview",
    "EnvironmentPushResult",
    "EnvironmentPushService",
    "EnvironmentSnapshot",
    "HarnessProject",
    "HarnessProjectConfig",
    "HarnessProjectState",
    "ImpactUncertainty",
    "ImpactUncertaintyKind",
    "ImpactedPythonFile",
    "InjectedStateMigrationCrash",
    "GitHubEnvironmentService",
    "GitHubEnvironmentSnapshot",
    "GovernedEnvironmentCommitService",
    "GovernedEnvironmentPullRequestService",
    "GovernedEnvironmentPushService",
    "HostedRepositoryHint",
    "LEGACY_BACKUP_SCHEMA_VERSION",
    "LEGACY_STATE_RELATIVE_PATH",
    "MANAGED_STATE_DIRECTORIES",
    "MAX_INCLUDED_MEMORY",
    "MAX_MEMORY_TAGS",
    "MAX_MEMORY_TEXT_CHARS",
    "MAX_PATCH_CHARS",
    "MAX_BOOTSTRAP_FILE_BYTES",
    "MINIMUM_READER_SCHEMA_VERSION",
    "MIGRATION_SUPPORT_RELATIVE_PATH",
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
    "PublicContractMarker",
    "PythonImpactIndex",
    "PythonImpactIndexCache",
    "PythonImpactResult",
    "RenderedProjectPreset",
    "RunDiffReview",
    "STATE_MIGRATION_ID",
    "STATE_MIGRATION_SCHEMA_VERSION",
    "STATE_LAYOUT_VERSION",
    "StateBackupResult",
    "StateRestoreResult",
    "StateMigrationResult",
    "StalePythonImpactIndexError",
    "TOOL_PROFILE_NAME_PATTERN",
    "TOOL_PROFILE_RESERVED_KEYS",
    "WorkspaceDiff",
    "WorkspaceExecution",
    "WorkspacePolicy",
    "WorktreeConflictError",
    "WorktreeError",
    "apply_run_diff",
    "analyze_python_impact",
    "capture_workspace_diff",
    "default_project_config_text",
    "detect_overlapping_run_diffs",
    "discard_run_worktree",
    "create_state_backup",
    "compile_python_impact_index",
    "init_project_config",
    "instructions_api",
    "load_project_config",
    "load_project_state",
    "memory_entries_to_context",
    "memory_entries_to_prompt",
    "memory_entry_from_dict",
    "memory_entry_to_dict",
    "migrate_legacy_state",
    "new_memory_id",
    "open_worktree_response",
    "parse_workspace_policy",
    "project_config_path",
    "project_config_to_dict",
    "project_id_for_root",
    "project_preset_to_dict",
    "project_state_from_dict",
    "project_state_path",
    "project_state_to_dict",
    "project_to_dict",
    "project_tool_profile_to_dict",
    "project_python_impact",
    "prepare_run_diff_merge",
    "prepare_runtime_state",
    "prepare_workspace_execution",
    "render_project_preset",
    "rendered_project_preset_to_dict",
    "reject_legacy_state_override",
    "restore_state_backup",
    "resolve_project",
    "resolve_workspace",
    "review_run_diff",
    "rollback_legacy_state",
    "run_diff_response",
    "save_project_state",
    "shlex_quote",
    "update_project_state",
    "utc_now",
    "verify_state_backup",
    "workspace_file_metadata",
    "workspace_tree",
    *(
        "InjectedProjectCatalogMigrationCrash MAX_CATALOG_ENTRIES MAX_CATALOG_PAGE_SIZE MAX_LAUNCH_PROFILES MAX_LAUNCH_PROFILE_PAGE_SIZE MAX_MIGRATION_SESSIONS "
        "PROJECT_CATALOG_SCHEMA_VERSION PROJECT_CATALOG_MIGRATION_ID PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION PROJECT_LAUNCH_PROFILE_SCHEMA_VERSION "
        "ProjectCatalogCapacityError ProjectCatalogConflictError ProjectCatalogEntryV1 ProjectCatalogError ProjectCatalogMigrationReceiptV1 ProjectCatalogMigrationService ProjectCatalogNotFoundError ProjectCatalogPageV1 ProjectCatalogService "
        "ProjectLocationRef ProjectLaunchProfileService ProjectLaunchProfileV1 ProjectRelocationPreviewV1 ResolvedProjectLaunchProfileV1 FilesystemProjectCatalogRepository FilesystemLaunchProfileRepository "
        "LaunchProfilePageV1 LaunchResolutionContextV1 SessionCatalogBindingService TerminalModeHint UnsatisfiedLaunchHintV1 catalog_entry_digest catalog_entry_from_dict catalog_entry_to_dict "
        "launch_profile_digest launch_profile_from_dict launch_profile_to_dict resolve_launch_profile resolved_project_location"
        " MIGRATION_REGISTRY_SCHEMA_VERSION NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1 MigrationRegistrationV1 validate_migration_sequence"
        " PROJECT_LAUNCH_READ_MODEL_SCHEMA_VERSION LaunchProfileReadPort ProjectCatalogReadPort ProjectLaunchReadModelV1 ProjectLaunchReadService InjectedNativeAgentGatewayMigrationCrash NATIVE_AGENT_GATEWAY_MIGRATION_ID NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION NativeAgentGatewayMigrationReceiptV1 NativeAgentGatewayMigrationService TEXTUAL_PREFERENCES_RETIREMENT_ID"
    ).split(),
]
