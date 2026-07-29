"""Project bootstrap assets and default configuration."""

from __future__ import annotations

from pathlib import Path

from .config import (
    _optional_text,
    default_project_config_text,
    load_project_config,
    project_config_path,
)
from .models import (
    DEFAULT_EVAL_DIR,
    DEFAULT_EVAL_SPECS,
    DEFAULT_PROMPT_TEMPLATE_DIR,
    DEFAULT_PROMPT_TEMPLATES,
    HarnessProjectConfig,
)
from .presets import _render_preset_template


def init_project_config(
    project_root: str | Path,
    *,
    project_name: str | None = None,
    overwrite: bool = False,
) -> HarnessProjectConfig:
    """Create a default non-secret project config if it is missing."""
    root = Path(project_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Project root does not exist: {root}")
    path = project_config_path(project_root)
    if path.exists() and not overwrite:
        return load_project_config(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = _optional_text(project_name) or root.name
    path.write_text(default_project_config_text(name), encoding="utf-8")
    _write_default_prompt_templates(root, overwrite=overwrite)
    _write_default_eval_specs(root, overwrite=overwrite)
    _write_default_agent_profiles(root, overwrite=overwrite)
    _write_default_workflows(root, overwrite=overwrite)
    return load_project_config(project_root)


def _write_default_prompt_templates(root: Path, *, overwrite: bool) -> None:
    prompt_dir = root / DEFAULT_PROMPT_TEMPLATE_DIR
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in DEFAULT_PROMPT_TEMPLATES.items():
        path = prompt_dir / filename
        if path.exists() and not overwrite:
            continue
        path.write_text(text, encoding="utf-8")


def _write_default_eval_specs(root: Path, *, overwrite: bool) -> None:
    eval_dir = root / DEFAULT_EVAL_DIR
    eval_dir.mkdir(parents=True, exist_ok=True)
    variables = {
        "project_name": root.name,
    }
    for filename, text in DEFAULT_EVAL_SPECS.items():
        path = eval_dir / filename
        if path.exists() and not overwrite:
            continue
        rendered = _render_preset_template(text, variables)
        path.write_text(rendered, encoding="utf-8")


def _write_default_agent_profiles(root: Path, *, overwrite: bool) -> None:
    from gpt2giga_harness.agents import (
        AGENT_DIRECTORY,
        STARTER_AGENT_PROFILES,
        render_starter_agent,
    )

    agent_dir = root / AGENT_DIRECTORY
    agent_dir.mkdir(parents=True, exist_ok=True)
    for agent_id in STARTER_AGENT_PROFILES:
        path = agent_dir / f"{agent_id}.yaml"
        if path.exists() and not overwrite:
            continue
        path.write_text(render_starter_agent(agent_id), encoding="utf-8")


def _write_default_workflows(root: Path, *, overwrite: bool) -> None:
    from gpt2giga_harness.workflows import (
        WORKFLOW_DIRECTORY,
        render_review_team_workflow,
    )

    workflow_dir = root / WORKFLOW_DIRECTORY
    workflow_dir.mkdir(parents=True, exist_ok=True)
    path = workflow_dir / "review-team.yaml"
    if not path.exists() or overwrite:
        path.write_text(render_review_team_workflow(), encoding="utf-8")
