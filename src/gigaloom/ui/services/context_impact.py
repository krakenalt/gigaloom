"""Read-only application ports for Context Lens and Impact Radar APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

from gigaloom.execution.api import NativeCodexContextProjection
from gigaloom.projects import api as projects_api


class ContextProjectionQuery(Protocol):
    """Owner-scoped source of one native session Context Lens projection."""

    def get_native_codex_context(
        self,
        *,
        session_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> NativeCodexContextProjection: ...


def compile_project_impact(
    *,
    workspace: str,
    changed_paths: Iterable[str],
) -> projects_api.PythonImpactResult:
    """Compile the bounded advisory projection for one exact Git worktree."""
    root = Path(workspace).expanduser().resolve()
    return projects_api.analyze_python_impact(root, changed_paths)


__all__ = ["ContextProjectionQuery", "compile_project_impact"]
