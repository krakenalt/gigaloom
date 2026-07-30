"""Immutable contracts and validation for environment commit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Mapping

ENVIRONMENT_COMMIT_SCHEMA_VERSION = 1


ENVIRONMENT_COMMIT_OWNER = "environment.commit"


MAX_COMMIT_MESSAGE_CHARS = 4096


MAX_AUTHOR_CHARS = 200


MAX_GIT_OUTPUT_BYTES = 1024 * 1024


GIT_MUTATION_TIMEOUT_SECONDS = 10.0


_HEX_SHA_RE = re.compile(r"[0-9a-f]{40,64}\Z")


_EMAIL_RE = re.compile(r"[^\s<>@]+@[^\s<>@]+\Z")


_PREVIEW_ID_RE = re.compile(r"commit_[0-9a-f]{64}\Z")


class EnvironmentCommitError(RuntimeError):
    """Content-free failure for one governed local commit action."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EnvironmentCommitPreview:
    """Immutable private preview of one exact local Git commit intent."""

    id: str
    repository_root: str
    worktree_root: str
    branch: str | None
    head: str | None
    diff_sha256: str
    remote: str | None
    staged_count: int
    message: str
    author_name: str
    author_email: str
    commit_date: str
    created_at: str
    schema_version: int = ENVIRONMENT_COMMIT_SCHEMA_VERSION

    @property
    def approval_binding(self) -> str:
        """Return the exact opaque binding consumed by the policy owner."""
        return f"environment-commit-v1:{self.id}"

    @property
    def scope_id(self) -> str:
        """Return a content-free project scope for allow-once approvals."""
        digest = hashlib.sha256(self.worktree_root.encode("utf-8")).hexdigest()
        return f"environment_{digest[:32]}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the explicit approval preview."""
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "repository_root": self.repository_root,
            "worktree_root": self.worktree_root,
            "branch": self.branch,
            "head": self.head,
            "diff_sha256": self.diff_sha256,
            "remote": self.remote,
            "target_branch": self.branch,
            "staged_count": self.staged_count,
            "message": self.message,
            "author": {"name": self.author_name, "email": self.author_email},
            "commit_date": self.commit_date,
            "created_at": self.created_at,
            "hooks_executed": False,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentCommitPreview:
        """Parse one strict private preview record."""
        author = payload.get("author")
        if not isinstance(author, Mapping):
            raise ValueError("commit preview author is invalid")
        preview = cls(
            schema_version=int(payload.get("schema_version", 0)),
            id=str(payload.get("id", "")),
            repository_root=str(payload.get("repository_root", "")),
            worktree_root=str(payload.get("worktree_root", "")),
            branch=_optional_text(payload.get("branch")),
            head=_optional_text(payload.get("head")),
            diff_sha256=str(payload.get("diff_sha256", "")),
            remote=_optional_text(payload.get("remote")),
            staged_count=int(payload.get("staged_count", -1)),
            message=str(payload.get("message", "")),
            author_name=str(author.get("name", "")),
            author_email=str(author.get("email", "")),
            commit_date=str(payload.get("commit_date", "")),
            created_at=str(payload.get("created_at", "")),
        )
        _validate_preview(preview)
        return preview


@dataclass(frozen=True)
class EnvironmentCommitResult:
    """Durable content-free completion evidence for one commit preview."""

    preview_id: str
    branch: str | None
    parent_head: str | None
    commit_head: str
    completed_at: str
    recovered: bool = False
    schema_version: int = ENVIRONMENT_COMMIT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialize bounded completion evidence."""
        return {
            "schema_version": self.schema_version,
            "preview_id": self.preview_id,
            "branch": self.branch,
            "parent_head": self.parent_head,
            "commit_head": self.commit_head,
            "completed_at": self.completed_at,
            "recovered": self.recovered,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentCommitResult:
        """Parse one strict durable completion record."""
        result = cls(
            schema_version=int(payload.get("schema_version", 0)),
            preview_id=str(payload.get("preview_id", "")),
            branch=_optional_text(payload.get("branch")),
            parent_head=_optional_text(payload.get("parent_head")),
            commit_head=str(payload.get("commit_head", "")),
            completed_at=str(payload.get("completed_at", "")),
            recovered=bool(payload.get("recovered", False)),
        )
        if result.schema_version != ENVIRONMENT_COMMIT_SCHEMA_VERSION:
            raise ValueError("unsupported commit result schema")
        if _PREVIEW_ID_RE.fullmatch(result.preview_id) is None:
            raise ValueError("commit result preview id is invalid")
        if _HEX_SHA_RE.fullmatch(result.commit_head) is None:
            raise ValueError("commit result head is invalid")
        if (
            result.parent_head is not None
            and _HEX_SHA_RE.fullmatch(result.parent_head) is None
        ):
            raise ValueError("commit result parent is invalid")
        _parse_timestamp(result.completed_at)
        return result


def _validate_preview(preview: EnvironmentCommitPreview) -> None:
    if preview.schema_version != ENVIRONMENT_COMMIT_SCHEMA_VERSION:
        raise ValueError("unsupported commit preview schema")
    _validate_preview_id(preview.id)
    for value in (preview.repository_root, preview.worktree_root):
        if not value or len(value) > 4096 or "\x00" in value:
            raise ValueError("commit preview path is invalid")
    if preview.branch is None or not preview.branch or len(preview.branch) > 512:
        raise ValueError("commit preview branch is invalid")
    if preview.head is not None and _HEX_SHA_RE.fullmatch(preview.head) is None:
        raise ValueError("commit preview head is invalid")
    if _HEX_SHA_RE.fullmatch(preview.diff_sha256) is None:
        raise ValueError("commit preview diff hash is invalid")
    if preview.staged_count < 1:
        raise ValueError("commit preview staged count is invalid")
    _validate_message(preview.message)
    _validate_author(preview.author_name, "author name")
    _validate_email(preview.author_email)
    if re.fullmatch(r"\d{1,20} [+-]\d{4}", preview.commit_date) is None:
        raise ValueError("commit preview date is invalid")
    _parse_timestamp(preview.created_at)


def _validate_preview_id(value: str) -> None:
    if _PREVIEW_ID_RE.fullmatch(str(value)) is None:
        raise EnvironmentCommitError("preview_invalid", "Commit preview is invalid.")


def _validate_message(value: str) -> str:
    text = str(value)
    if (
        not text.strip()
        or len(text) > MAX_COMMIT_MESSAGE_CHARS
        or "\x00" in text
        or any(ord(character) < 32 and character not in "\n\t" for character in text)
    ):
        raise EnvironmentCommitError("message_invalid", "Commit message is invalid.")
    return text


def _validate_author(value: str, field: str) -> str:
    text = str(value).strip()
    if (
        not text
        or len(text) > MAX_AUTHOR_CHARS
        or any(ord(character) < 32 for character in text)
        or any(character in "<>\n\r" for character in text)
    ):
        raise EnvironmentCommitError("author_invalid", f"Commit {field} is invalid.")
    return text


def _validate_email(value: str) -> str:
    text = str(value).strip()
    if (
        len(text) > MAX_AUTHOR_CHARS
        or any(ord(character) < 32 for character in text)
        or _EMAIL_RE.fullmatch(text) is None
    ):
        raise EnvironmentCommitError(
            "author_invalid", "Commit author email is invalid."
        )
    return text


def _mapping_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _parse_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("timestamp is invalid")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("timestamp is invalid")
    return parsed


def _utc_now(clock: Callable[[], datetime]) -> str:
    return (
        clock()
        .astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
