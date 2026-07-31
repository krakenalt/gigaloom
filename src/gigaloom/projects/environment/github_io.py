"""Bounded GitHub CLI execution."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any

from .github_contracts import (
    GitHubEnrichmentError,
    MAX_GITHUB_OUTPUT_BYTES,
    _CommandResult,
)


def _run_gh_command(
    command: tuple[str, ...],
    cwd: Path,
    timeout_seconds: float,
    cancel_event: threading.Event,
) -> _CommandResult:
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
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()

    def drain(stream: Any, target: bytearray) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            remaining = MAX_GITHUB_OUTPUT_BYTES + 1 - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
            if len(target) > MAX_GITHUB_OUTPUT_BYTES or len(chunk) > remaining:
                overflow.set()

    threads = (
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    )
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None:
            if cancel_event.wait(
                timeout=min(0.05, max(deadline - time.monotonic(), 0.0))
            ):
                process.kill()
                process.wait()
                raise GitHubEnrichmentError(
                    "cancelled", "GitHub enrichment was cancelled."
                )
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise GitHubEnrichmentError(
                    "github_timeout", "GitHub enrichment timed out."
                )
            if overflow.is_set():
                process.kill()
                process.wait()
                raise GitHubEnrichmentError(
                    "github_output_limit", "GitHub output exceeded its limit."
                )
        returncode = int(process.returncode or 0)
    finally:
        for thread in threads:
            thread.join(timeout=1)
    if overflow.is_set():
        raise GitHubEnrichmentError(
            "github_output_limit", "GitHub output exceeded its limit."
        )
    return _CommandResult(returncode, bytes(stdout), bytes(stderr))
