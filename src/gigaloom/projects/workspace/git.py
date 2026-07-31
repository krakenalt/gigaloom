"""Shell-free Git primitives for workspace isolation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .contracts import WorktreeError


def _worktree_path(data_dir: str | Path, session_id: str, run_id: str) -> Path:
    return (
        Path(data_dir).expanduser()
        / "worktrees"
        / _safe_path_part(session_id)
        / _safe_path_part(run_id)
    )


def _safe_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in "_-" else "_" for char in value)


def _workspace_execution_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    value = metadata.get("workspace_execution")
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _required_metadata_text(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    if value is None or not str(value).strip():
        raise WorktreeError(f"Run worktree metadata is missing {key}.")
    return str(value)


def _optional_branch_name(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-.")
    if any(char not in allowed for char in text) or text.startswith("-"):
        raise WorktreeError("branch_name contains unsupported characters.")
    return text


def _git_status(cwd: str) -> tuple[tuple[str, str], ...] | None:
    result = _git_run(cwd, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        return None
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        rows.append((line[:2], line[3:]))
    return tuple(rows)


def _git_output(cwd: str, *args: str) -> str | None:
    result = _git_run(cwd, *args)
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _git_run(
    cwd: str,
    *args: str,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 10,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("git", "-C", cwd, *args),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, **dict(env or {})},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            ("git", "-C", cwd, *args),
            returncode=1,
            stdout="",
            stderr=str(exc),
        )


def _git_apply(
    cwd: str,
    patch: str,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    return _git_run(
        cwd,
        "apply",
        "--binary",
        *extra_args,
        "-",
        input_text=patch,
        timeout=20,
    )


def _stderr(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip()[-1000:]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
