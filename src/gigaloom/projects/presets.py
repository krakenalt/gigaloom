"""Rendering of reviewed project presets and prompt templates."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .models import (
    HarnessProject,
    HarnessProjectConfig,
    ProjectPreset,
    RenderedProjectPreset,
    _PRESET_VARIABLE_NAMES,
)


def render_project_preset(
    project: HarnessProject,
    config: HarnessProjectConfig,
    name: str,
    *,
    user_prompt: str | None = None,
    selected_files: tuple[str, ...] = (),
    last_run_diff: str | None = None,
) -> RenderedProjectPreset:
    """Render a project preset prompt with safe project variables."""
    try:
        preset = config.presets[name]
    except KeyError as exc:
        raise KeyError(f"Unknown project preset: {name}") from exc
    template, source = _preset_prompt_template(project.root, preset)
    merged_selected_files = tuple(
        dict.fromkeys((*preset.selected_files, *selected_files))
    )
    variables = _preset_variables(
        project,
        selected_files=merged_selected_files,
        last_run_diff=last_run_diff,
        user_prompt=user_prompt,
    )
    prompt = _render_preset_template(template, variables).strip()
    return RenderedProjectPreset(
        name=name,
        title=preset.title,
        prompt=prompt,
        prompt_source=source,
        harness=preset.harness,
        model=preset.model,
        api_mode=preset.api_mode,
        mode=preset.mode,
        invocation_mode=preset.invocation_mode,
        workspace_policy=preset.workspace_policy,
        selected_files=merged_selected_files,
        attachment_rules=preset.attachment_rules,
        variables=_public_preset_variables(variables),
        warnings=_preset_render_warnings(prompt, preset, source),
    )


def _preset_prompt_template(
    project_root: str,
    preset: ProjectPreset,
) -> tuple[str, str]:
    if preset.prompt is not None:
        return preset.prompt, "prompt"
    if preset.prompt_file is None:
        return "{{user_prompt}}", "user_prompt"
    prompt_path = _resolve_prompt_file(project_root, preset.prompt_file)
    try:
        return prompt_path.read_text(encoding="utf-8"), preset.prompt_file
    except FileNotFoundError as exc:
        raise ValueError(
            f"Preset prompt_file does not exist: {preset.prompt_file}"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Preset prompt_file cannot be read: {preset.prompt_file}"
        ) from exc


def _resolve_prompt_file(project_root: str, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError("Preset prompt_file must be relative to the project root")
    root = Path(project_root).expanduser().resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "Preset prompt_file must stay inside the project root"
        ) from exc
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path


def _preset_variables(
    project: HarnessProject,
    *,
    selected_files: tuple[str, ...],
    last_run_diff: str | None,
    user_prompt: str | None,
) -> dict[str, str]:
    return {
        "project_name": project.name,
        "branch": project.git_branch or "",
        "selected_files": "\n".join(selected_files),
        "selected_files_inline": ", ".join(selected_files),
        "last_run_diff": last_run_diff or "",
        "user_prompt": user_prompt or "",
    }


def _public_preset_variables(variables: Mapping[str, str]) -> dict[str, Any]:
    return {
        "project_name": variables.get("project_name") or "",
        "branch": variables.get("branch") or "",
        "selected_files": [
            line
            for line in str(variables.get("selected_files") or "").splitlines()
            if line
        ],
        "has_last_run_diff": bool(variables.get("last_run_diff")),
        "has_user_prompt": bool(variables.get("user_prompt")),
    }


def _render_preset_template(
    template: str,
    variables: Mapping[str, str],
) -> str:
    rendered = template
    for name in _PRESET_VARIABLE_NAMES:
        value = str(variables.get(name) or "")
        rendered = re.sub(r"{{\s*" + re.escape(name) + r"\s*}}", value, rendered)
        rendered = rendered.replace("${" + name + "}", value)
        rendered = re.sub(
            r"(?<![A-Za-z0-9_])\$" + re.escape(name) + r"\b", value, rendered
        )
    return rendered


def _preset_render_warnings(
    prompt: str,
    preset: ProjectPreset,
    source: str,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if not prompt.strip():
        warnings.append("Preset rendered an empty prompt.")
    if preset.prompt_file and source == preset.prompt_file and preset.prompt:
        warnings.append("Preset prompt_file was ignored because inline prompt is set.")
    return tuple(warnings)
