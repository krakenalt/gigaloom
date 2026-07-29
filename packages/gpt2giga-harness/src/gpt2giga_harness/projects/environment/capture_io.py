"""Bounded Git command and parsing helpers for environment capture."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import threading
from typing import Any
from urllib.parse import urlsplit


from .models import (
    EnvironmentCaptureError,
    HostedRepositoryHint,
    MAX_COMMAND_OUTPUT_BYTES,
    MAX_DIFF_HASH_BYTES,
    _HEX_SHA_RE,
    _validate_bounded_text,
)


@dataclass(frozen=True)
class _StatusSnapshot:
    branch: str | None
    detached: bool
    head: str | None
    upstream: str | None
    ahead: int
    behind: int
    staged_count: int
    unstaged_count: int
    untracked_count: int
    paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _parse_status(payload: bytes) -> _StatusSnapshot:
    branch = None
    detached = False
    head = None
    upstream = None
    ahead = 0
    behind = 0
    staged = 0
    unstaged = 0
    untracked = 0
    paths: list[str] = []
    untracked_paths: list[str] = []
    records = payload.split(b"\0")
    index = 0
    while index < len(records):
        raw = records[index]
        index += 1
        if not raw:
            continue
        record = raw.decode("utf-8", "replace")
        if record.startswith("# branch.oid "):
            value = record.removeprefix("# branch.oid ")
            head = value if _HEX_SHA_RE.fullmatch(value) else None
            continue
        if record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ")
            detached = value == "(detached)"
            branch = None if detached or value == "(initial)" else value
            continue
        if record.startswith("# branch.upstream "):
            upstream = record.removeprefix("# branch.upstream ") or None
            continue
        if record.startswith("# branch.ab "):
            match = re.fullmatch(r"# branch\.ab \+(\d+) -(\d+)", record)
            if match is None:
                raise EnvironmentCaptureError(
                    "git_output_invalid", "Git branch status output is invalid."
                )
            ahead, behind = (int(match.group(1)), int(match.group(2)))
            continue
        if record.startswith(("1 ", "2 ", "u ")):
            fields = record.split(" ")
            xy = fields[1] if len(fields) > 1 else ""
            path_index = (
                8 if record.startswith("1 ") else 9 if record.startswith("2 ") else 10
            )
            if len(xy) != 2 or len(fields) <= path_index:
                raise EnvironmentCaptureError(
                    "git_output_invalid", "Git worktree status output is invalid."
                )
            staged += xy[0] != "."
            unstaged += xy[1] != "."
            paths.append(" ".join(fields[path_index:]))
            if record.startswith("2 "):
                index += 1  # discard the rename source path; destination is canonical
            continue
        if record.startswith("? "):
            untracked += 1
            paths.append(record[2:])
            untracked_paths.append(record[2:])
            continue
        raise EnvironmentCaptureError(
            "git_output_invalid", "Git status output is invalid."
        )
    return _StatusSnapshot(
        branch=branch,
        detached=detached,
        head=head,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        staged_count=staged,
        unstaged_count=unstaged,
        untracked_count=untracked,
        paths=tuple(paths),
        untracked_paths=tuple(untracked_paths),
    )


def _parse_hosted_repository(value: str) -> HostedRepositoryHint | None:
    """Discard credentials and accept only an exact host/owner/repository tuple."""
    if not value or len(value) > 4096 or any(ord(char) < 32 for char in value):
        return None
    host = ""
    repository_path = ""
    if "://" in value:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https", "ssh"}:
            return None
        host = parsed.hostname or ""
        repository_path = parsed.path
    else:
        match = re.fullmatch(r"(?:[^@/:\s]+@)?([^/:\s]+):/?([^\s]+)", value)
        if match is None:
            return None
        host, repository_path = match.groups()
    host = host.casefold().rstrip(".")
    path = repository_path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    try:
        return HostedRepositoryHint(host=host, name_with_owner="/".join(parts))
    except ValueError:
        return None


def _push_blocker(
    *, branch: str | None, detached: bool, head: str | None, remote: str | None
) -> str | None:
    if head is None:
        return "unborn_head"
    if detached or branch is None:
        return "detached_head"
    if remote is None:
        return "remote_unavailable"
    return None


def _hash_untracked_files(root: Path, paths: tuple[str, ...], digest: Any) -> None:
    total_bytes = 0
    resolved_root = root.resolve()
    for relative in sorted(paths):
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        try:
            parent = candidate.parent.resolve(strict=True)
            metadata = candidate.lstat()
        except OSError as exc:
            raise EnvironmentCaptureError(
                "snapshot_stale", "Untracked Git state changed during inspection."
            ) from exc
        if not parent.is_relative_to(resolved_root):
            raise EnvironmentCaptureError(
                "path_unsafe", "Untracked Git path leaves the worktree."
            )
        digest.update(b"untracked\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if stat.S_ISLNK(metadata.st_mode):
            try:
                payload = os.fsencode(os.readlink(candidate))
            except OSError as exc:
                raise EnvironmentCaptureError(
                    "snapshot_stale", "Untracked Git state changed during inspection."
                ) from exc
            total_bytes += len(payload)
            if total_bytes > MAX_DIFF_HASH_BYTES:
                raise EnvironmentCaptureError(
                    "diff_limit", "Untracked Git content exceeds the hash limit."
                )
            digest.update(payload)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise EnvironmentCaptureError(
                "path_unsafe", "Untracked Git path has an unsupported file type."
            )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(candidate, flags)
        except OSError as exc:
            raise EnvironmentCaptureError(
                "snapshot_stale", "Untracked Git state changed during inspection."
            ) from exc
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise EnvironmentCaptureError(
                    "snapshot_stale", "Untracked Git state changed during inspection."
                )
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_DIFF_HASH_BYTES:
                    raise EnvironmentCaptureError(
                        "diff_limit", "Untracked Git content exceeds the hash limit."
                    )
                digest.update(chunk)
            completed = os.fstat(stream.fileno())
        if (
            completed.st_size != opened.st_size
            or completed.st_mtime_ns != opened.st_mtime_ns
        ):
            raise EnvironmentCaptureError(
                "snapshot_stale", "Untracked Git state changed during inspection."
            )


def _run_bounded_command(
    command: tuple[str, ...], *, timeout: float, max_output_bytes: int
) -> _CommandResult:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
    )
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()

    def drain(stream, target: bytearray) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            remaining = max_output_bytes + 1 - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
            if len(target) > max_output_bytes or len(chunk) > remaining:
                overflow.set()

    threads = (
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    )
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise EnvironmentCaptureError(
            "git_timeout", "Git environment inspection timed out."
        ) from exc
    finally:
        for thread in threads:
            thread.join(timeout=1)
    if overflow.is_set():
        raise EnvironmentCaptureError(
            "output_limit", "Git environment inspection exceeded its output limit."
        )
    return _CommandResult(returncode, bytes(stdout), bytes(stderr))


def _hash_command_output(
    command: tuple[str, ...],
    *,
    digest: Any,
    timeout: float,
    max_bytes: int,
) -> str:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
    )
    state: dict[str, Any] = {"bytes": 0, "overflow": False}
    stderr = bytearray()

    def hash_stdout() -> None:
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            state["bytes"] += len(chunk)
            if state["bytes"] <= max_bytes:
                digest.update(chunk)
            else:
                state["overflow"] = True

    def drain_stderr() -> None:
        while True:
            chunk = process.stderr.read(65536)
            if not chunk:
                return
            remaining = MAX_COMMAND_OUTPUT_BYTES + 1 - len(stderr)
            if remaining > 0:
                stderr.extend(chunk[:remaining])

    threads = (
        threading.Thread(target=hash_stdout, daemon=True),
        threading.Thread(target=drain_stderr, daemon=True),
    )
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise EnvironmentCaptureError(
            "git_timeout", "Git diff hashing timed out."
        ) from exc
    finally:
        for thread in threads:
            thread.join(timeout=1)
    if state["overflow"]:
        raise EnvironmentCaptureError("diff_limit", "Git diff exceeds the hash limit.")
    if returncode != 0:
        raise EnvironmentCaptureError("git_failed", "Git diff hashing failed.")
    return digest.hexdigest()


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _is_safe_ref_text(value: str) -> bool:
    try:
        _validate_bounded_text(value, "ref")
    except ValueError:
        return False
    return True
