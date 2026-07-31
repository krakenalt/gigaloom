"""Durable non-secret project preferences and cockpit state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .config import (
    _optional_text,
    _parse_optional_api_mode,
    _parse_optional_invocation_mode,
)
from .models import HarnessProject, HarnessProjectState, PROJECT_STATE_FILE


def load_project_state(project: HarnessProject) -> HarnessProjectState:
    """Load mutable project UI state from transparent JSON."""
    path = project_state_path(project)
    try:
        data = _read_json(path)
    except (FileNotFoundError, ValueError, OSError):
        return HarnessProjectState()
    return project_state_from_dict(data)


def save_project_state(
    project: HarnessProject,
    state: HarnessProjectState,
) -> HarnessProjectState:
    """Persist mutable project UI state."""
    path = project_state_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, project_state_to_dict(state))
    return state


def update_project_state(
    project: HarnessProject,
    patch: Mapping[str, Any],
) -> HarnessProjectState:
    """Apply an allowlisted patch to mutable project UI state."""
    current = project_state_to_dict(load_project_state(project))
    for key in (
        "last_harness",
        "last_model",
        "last_api_mode",
        "last_run_mode",
        "last_invocation_mode",
        "last_selected_session",
        "trusted",
    ):
        if key in patch:
            current[key] = patch[key]
    state = project_state_from_dict(current)
    return save_project_state(project, state)


def project_state_path(project: HarnessProject) -> Path:
    """Return the mutable state path for a project."""
    return Path(project.state_dir).expanduser() / PROJECT_STATE_FILE


def project_state_to_dict(state: HarnessProjectState) -> dict[str, Any]:
    """Serialize mutable project UI state."""
    return {
        "last_harness": state.last_harness,
        "last_model": state.last_model,
        "last_api_mode": (
            state.last_api_mode.value if state.last_api_mode is not None else None
        ),
        "last_run_mode": state.last_run_mode,
        "last_invocation_mode": (
            state.last_invocation_mode.value
            if state.last_invocation_mode is not None
            else None
        ),
        "last_selected_session": state.last_selected_session,
        "trusted": state.trusted,
    }


def project_state_from_dict(data: Mapping[str, Any]) -> HarnessProjectState:
    """Parse mutable project UI state from JSON-compatible data."""
    api_mode = _parse_optional_api_mode(data.get("last_api_mode"))
    invocation_mode = _parse_optional_invocation_mode(data.get("last_invocation_mode"))
    return HarnessProjectState(
        last_harness=_optional_text(data.get("last_harness")),
        last_model=_optional_text(data.get("last_model")),
        last_api_mode=api_mode,
        last_run_mode=_optional_text(data.get("last_run_mode")),
        last_invocation_mode=invocation_mode,
        last_selected_session=_optional_text(data.get("last_selected_session")),
        trusted=data.get("trusted") if isinstance(data.get("trusted"), bool) else None,
    )


def _read_json(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, Mapping):
        raise ValueError("Project state must be a JSON object")
    return data


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
