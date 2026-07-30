"""Bounded command execution helpers for environment push."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
from typing import Mapping

from .models import EnvironmentSnapshot
from .push_contracts import (
    EnvironmentPushError,
    EnvironmentPushPreview,
    GIT_PUSH_TIMEOUT_SECONDS,
    MAX_GIT_OUTPUT_BYTES,
)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _run_bounded(
    command: tuple[str, ...], *, environment: Mapping[str, str]
) -> _CommandResult:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment),
    )
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()

    def drain(stream, target: bytearray) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            remaining = MAX_GIT_OUTPUT_BYTES + 1 - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
            if len(target) > MAX_GIT_OUTPUT_BYTES or len(chunk) > remaining:
                overflow.set()

    threads = (
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    )
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=GIT_PUSH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise EnvironmentPushError("git_timeout", "Git push action timed out.") from exc
    finally:
        for thread in threads:
            thread.join(timeout=1)
    if overflow.is_set():
        raise EnvironmentPushError(
            "output_limit", "Git push action exceeded its output limit."
        )
    return _CommandResult(returncode, bytes(stdout), bytes(stderr))


def _snapshot_matches_preview(
    snapshot: EnvironmentSnapshot, preview: EnvironmentPushPreview
) -> bool:
    return (
        snapshot.repository_root == preview.repository_root
        and snapshot.worktree_root == preview.worktree_root
        and snapshot.branch == preview.branch
        and snapshot.head == preview.head
        and snapshot.diff_sha256 == preview.diff_sha256
        and snapshot.remote == preview.remote
        and snapshot.upstream == preview.upstream
        and snapshot.ahead == preview.ahead
        and snapshot.behind == preview.behind
        and not snapshot.detached
    )
