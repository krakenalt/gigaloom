"""Bounded command execution helpers for environment pull request."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Mapping

from .pull_request_contracts import (
    EnvironmentPullRequestError,
    MAX_HOSTED_OUTPUT_BYTES,
    _CommandResult,
)


def _run_hosted_command(
    command: tuple[str, ...],
    cwd: Path,
    input_bytes: bytes | None,
    timeout_seconds: float,
    *,
    environment: Mapping[str, str] | None = None,
) -> _CommandResult:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment or _noninteractive_environment()),
    )
    try:
        stdout, stderr = process.communicate(input_bytes, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise EnvironmentPullRequestError(
            "hosted_timeout", "Hosted command timed out."
        ) from exc
    if len(stdout) > MAX_HOSTED_OUTPUT_BYTES or len(stderr) > MAX_HOSTED_OUTPUT_BYTES:
        raise EnvironmentPullRequestError(
            "hosted_output_limit", "Hosted command exceeded its output limit."
        )
    return _CommandResult(process.returncode, stdout, stderr)


def _noninteractive_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "GH_PROMPT_DISABLED": "1",
            "GH_PAGER": "cat",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "CLICOLOR": "0",
            "NO_COLOR": "1",
        }
    )
    return environment


def _resolve_executable(value: str | None) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path)
