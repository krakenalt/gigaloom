"""Stable public serialization for project values."""

from __future__ import annotations

from typing import Any

from gigaloom.types import redact_secrets

from .models import (
    HarnessProject,
    HarnessProjectConfig,
    ProjectPreset,
    ProjectToolProfile,
    RenderedProjectPreset,
)


def project_to_dict(project: HarnessProject) -> dict[str, Any]:
    """Serialize project context for API responses."""
    return {
        "id": project.id,
        "root": project.root,
        "name": project.name,
        "git_root": project.git_root,
        "git_branch": project.git_branch,
        "is_git_repo": project.is_git_repo,
        "dirty_summary": dict(project.dirty_summary),
        "config_path": project.config_path,
        "state_dir": project.state_dir,
    }


def project_config_to_dict(config: HarnessProjectConfig) -> dict[str, Any]:
    """Serialize project config for API responses."""
    return {
        "path": config.path,
        "exists": config.exists,
        "project_name": config.project_name,
        "defaults": {
            "harness": config.defaults.harness,
            "model": config.defaults.model,
            "api_mode": config.defaults.api_mode.value,
            "mode": config.defaults.mode,
        },
        "harnesses": {"enabled": list(config.enabled_harnesses)},
        "presets": {
            name: project_preset_to_dict(name, preset)
            for name, preset in config.presets.items()
        },
        "tools": {
            name: project_tool_profile_to_dict(name, profile)
            for name, profile in config.tool_profiles.items()
        },
        "editor": {
            "command": config.editor.command,
            "terminal_command": config.editor.terminal_command,
        },
        "attachments": {
            "max_file_mb": config.attachments.max_file_mb,
            "max_total_mb_per_run": config.attachments.max_total_mb_per_run,
            "allow_images": config.attachments.allow_images,
            "allow_documents": config.attachments.allow_documents,
            "allow_binary": config.attachments.allow_binary,
            "respect_gitignore": config.attachments.respect_gitignore,
            "ignore": list(config.attachments.ignore),
        },
    }


def project_tool_profile_to_dict(
    name: str,
    profile: ProjectToolProfile,
) -> dict[str, Any]:
    """Serialize one project tool profile without exposing secrets."""
    return {
        "name": name,
        "enabled": profile.enabled,
        "title": profile.title,
        "kind": profile.kind,
        "description": profile.description,
        "harnesses": list(profile.harnesses),
        "config": redact_secrets(dict(profile.config)),
    }


def project_preset_to_dict(name: str, preset: ProjectPreset) -> dict[str, Any]:
    """Serialize one project preset for API and CLI responses."""
    return {
        "name": name,
        "title": preset.title,
        "harness": preset.harness,
        "model": preset.model,
        "api_mode": preset.api_mode.value if preset.api_mode is not None else None,
        "mode": preset.mode,
        "invocation_mode": (
            preset.invocation_mode.value if preset.invocation_mode is not None else None
        ),
        "workspace_policy": preset.workspace_policy,
        "prompt": preset.prompt,
        "prompt_file": preset.prompt_file,
        "selected_files": list(preset.selected_files),
        "attachment_rules": dict(preset.attachment_rules),
    }


def rendered_project_preset_to_dict(
    preset: RenderedProjectPreset,
) -> dict[str, Any]:
    """Serialize a rendered project preset."""
    payload = {
        "name": preset.name,
        "title": preset.title,
        "harness": preset.harness,
        "model": preset.model,
        "api_mode": preset.api_mode.value if preset.api_mode is not None else None,
        "mode": preset.mode,
        "invocation_mode": (
            preset.invocation_mode.value if preset.invocation_mode is not None else None
        ),
        "workspace_policy": preset.workspace_policy,
        "prompt": preset.prompt,
        "prompt_source": preset.prompt_source,
        "selected_files": list(preset.selected_files),
        "attachment_rules": dict(preset.attachment_rules),
        "variables": dict(preset.variables),
        "warnings": list(preset.warnings),
    }
    payload["run"] = {
        "harness_id": preset.harness,
        "model": preset.model,
        "api_mode": payload["api_mode"],
        "mode": preset.mode,
        "invocation_mode": payload["invocation_mode"],
        "workspace_policy": preset.workspace_policy,
        "prompt": preset.prompt,
    }
    return payload
