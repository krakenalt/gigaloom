"""Project root and Git identity resolution."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from typing import Mapping

from gigaloom.config import DEFAULT_HARNESS_DATA_DIR
from gigaloom.safe_paths import resolve_operator_path

from .config import load_project_config, project_config_path
from .models import HarnessProject


def resolve_project(
    workspace: str | Path | None = None,
    *,
    data_dir: str | Path = DEFAULT_HARNESS_DATA_DIR,
    load_config_name: bool = True,
) -> HarnessProject:
    """Resolve workspace identity, preferring the enclosing git root."""
    workspace_path = _resolve_workspace_path(workspace)
    git_root_path = _git_root(workspace_path)
    root_path = git_root_path or workspace_path
    project_id = project_id_for_root(root_path)
    config_path = project_config_path(root_path)
    project_config = (
        load_project_config(root_path)
        if load_config_name and config_path.exists()
        else None
    )
    project_name = (
        project_config.project_name
        if project_config and project_config.project_name
        else root_path.name
    )
    state_dir = Path(data_dir).expanduser() / "projects" / project_id
    return HarnessProject(
        id=project_id,
        root=str(root_path),
        name=project_name or str(root_path),
        git_root=str(git_root_path) if git_root_path is not None else None,
        git_branch=_git_branch(root_path) if git_root_path is not None else None,
        is_git_repo=git_root_path is not None,
        dirty_summary=_git_dirty_summary(root_path)
        if git_root_path is not None
        else {},
        config_path=str(config_path) if config_path.exists() else None,
        state_dir=str(state_dir),
    )


def project_id_for_root(project_root: str | Path) -> str:
    """Return the stable project id for a normalized project root."""
    normalized = str(Path(project_root).expanduser().resolve())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"proj_{digest[:16]}"


def _resolve_workspace_path(workspace: str | Path | None) -> Path:
    if workspace is None:
        resolved = Path.cwd()
    else:
        resolved = resolve_operator_path(workspace)
    if resolved.is_file():
        return resolved.parent
    return resolved


def _git_root(workspace_path: Path) -> Path | None:
    value = _git_output(("rev-parse", "--show-toplevel"), cwd=workspace_path)
    return Path(value).resolve() if value else None


def _git_branch(project_root: Path) -> str | None:
    return _git_output(("branch", "--show-current"), cwd=project_root)


def _git_dirty_summary(project_root: Path) -> Mapping[str, int]:
    status = _git_output(("status", "--porcelain=v1"), cwd=project_root)
    summary = {"added": 0, "deleted": 0, "changed": 0}
    if not status:
        return summary
    for line in status.splitlines():
        code = line[:2]
        if code == "??" or "A" in code:
            summary["added"] += 1
        elif "D" in code:
            summary["deleted"] += 1
        else:
            summary["changed"] += 1
    return summary


def _git_output(args: tuple[str, ...], *, cwd: Path) -> str | None:
    if not cwd.exists():
        return None
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None
