"""Project response projections owned by the UI application layer."""

from __future__ import annotations

from typing import Any

from gpt2giga_harness.project import (
    load_project_config,
    load_project_state,
    project_config_to_dict,
    project_state_to_dict,
    project_to_dict,
    resolve_project,
)


def project_response(workspace: str | None, data_dir: str) -> dict[str, Any]:
    """Build the public project configuration and state projection."""
    project_context = resolve_project(workspace, data_dir=data_dir)
    loaded = load_project_config(project_context.root)
    config_payload = project_config_to_dict(loaded)
    return {
        "project": project_to_dict(project_context),
        "config": config_payload,
        "state": project_state_to_dict(load_project_state(project_context)),
        "defaults": config_payload["defaults"],
        "presets": list(config_payload["presets"].values()),
        "tools": list(config_payload["tools"].values()),
    }
