"""Immutable contracts and validation for environment push."""

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

from .models import HostedRepositoryHint

ENVIRONMENT_PUSH_SCHEMA_VERSION = 1


ENVIRONMENT_PUSH_OWNER = "environment.push"


MAX_GIT_OUTPUT_BYTES = 1024 * 1024


GIT_PUSH_TIMEOUT_SECONDS = 30.0


_HEX_SHA_RE = re.compile(r"[0-9a-f]{40,64}\Z")


_PREVIEW_ID_RE = re.compile(r"push_[0-9a-f]{64}\Z")


_REF_COMPONENT_RE = re.compile(r"(?!-)(?!.*\.\.)(?!.*@\{)[^\x00-\x20~^:?*\\]+\Z")


class EnvironmentPushError(RuntimeError):
    """Content-free failure for one governed remote push action."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EnvironmentPushPreview:
    """Immutable private preview of one exact non-force Git push intent."""

    id: str
    repository_root: str
    worktree_root: str
    repository_host: str
    repository_name: str
    branch: str
    head: str
    diff_sha256: str
    remote: str
    upstream: str | None
    target_branch: str
    remote_ref: str
    remote_head: str | None
    ahead: int
    behind: int
    set_upstream: bool
    created_at: str
    schema_version: int = ENVIRONMENT_PUSH_SCHEMA_VERSION

    @property
    def approval_binding(self) -> str:
        """Return the exact opaque binding consumed by the policy owner."""
        return f"environment-push-v1:{self.id}"

    @property
    def scope_id(self) -> str:
        """Return a content-free project scope for allow-once approvals."""
        digest = hashlib.sha256(self.worktree_root.encode("utf-8")).hexdigest()
        return f"environment_{digest[:32]}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the explicit remote-write approval preview."""
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "repository_root": self.repository_root,
            "worktree_root": self.worktree_root,
            "repository": {
                "host": self.repository_host,
                "name_with_owner": self.repository_name,
            },
            "branch": self.branch,
            "head": self.head,
            "diff_sha256": self.diff_sha256,
            "remote": self.remote,
            "upstream": self.upstream,
            "target_branch": self.target_branch,
            "remote_ref": self.remote_ref,
            "remote_head": self.remote_head,
            "ahead": self.ahead,
            "behind": self.behind,
            "permissions": {
                "network_connect": True,
                "remote_write": True,
                "create_remote_branch": self.remote_head is None,
                "set_upstream": self.set_upstream,
                "force_update": False,
                "delete_remote_branch": False,
                "follow_tags": False,
                "execute_hooks": False,
            },
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentPushPreview:
        """Parse one strict private preview record."""
        repository = payload.get("repository")
        if not isinstance(repository, Mapping):
            raise ValueError("push preview repository is invalid")
        preview = cls(
            schema_version=int(payload.get("schema_version", 0)),
            id=str(payload.get("id", "")),
            repository_root=str(payload.get("repository_root", "")),
            worktree_root=str(payload.get("worktree_root", "")),
            repository_host=str(repository.get("host", "")),
            repository_name=str(repository.get("name_with_owner", "")),
            branch=str(payload.get("branch", "")),
            head=str(payload.get("head", "")),
            diff_sha256=str(payload.get("diff_sha256", "")),
            remote=str(payload.get("remote", "")),
            upstream=_optional_text(payload.get("upstream")),
            target_branch=str(payload.get("target_branch", "")),
            remote_ref=str(payload.get("remote_ref", "")),
            remote_head=_optional_text(payload.get("remote_head")),
            ahead=int(payload.get("ahead", -1)),
            behind=int(payload.get("behind", -1)),
            set_upstream=bool(payload.get("permissions", {}).get("set_upstream"))
            if isinstance(payload.get("permissions"), Mapping)
            else False,
            created_at=str(payload.get("created_at", "")),
        )
        _validate_preview(preview)
        return preview


@dataclass(frozen=True)
class EnvironmentPushResult:
    """Durable completion evidence for one exact remote commit update."""

    preview_id: str
    remote: str
    branch: str
    target_branch: str
    remote_ref: str
    commit_head: str
    remote_commit_url: str
    run_evidence_url: str
    upstream_configured: bool
    completed_at: str
    recovered: bool = False
    schema_version: int = ENVIRONMENT_PUSH_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialize bounded remote completion evidence."""
        return {
            "schema_version": self.schema_version,
            "preview_id": self.preview_id,
            "remote": self.remote,
            "branch": self.branch,
            "target_branch": self.target_branch,
            "remote_ref": self.remote_ref,
            "commit_head": self.commit_head,
            "remote_commit_url": self.remote_commit_url,
            "run_evidence_url": self.run_evidence_url,
            "upstream_configured": self.upstream_configured,
            "completed_at": self.completed_at,
            "recovered": self.recovered,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentPushResult:
        """Parse one strict durable completion record."""
        result = cls(
            schema_version=int(payload.get("schema_version", 0)),
            preview_id=str(payload.get("preview_id", "")),
            remote=str(payload.get("remote", "")),
            branch=str(payload.get("branch", "")),
            target_branch=str(payload.get("target_branch", "")),
            remote_ref=str(payload.get("remote_ref", "")),
            commit_head=str(payload.get("commit_head", "")),
            remote_commit_url=str(payload.get("remote_commit_url", "")),
            run_evidence_url=str(payload.get("run_evidence_url", "")),
            upstream_configured=bool(payload.get("upstream_configured", False)),
            completed_at=str(payload.get("completed_at", "")),
            recovered=bool(payload.get("recovered", False)),
        )
        _validate_result(result)
        return result


def _validate_preview(preview: EnvironmentPushPreview) -> None:
    if preview.schema_version != ENVIRONMENT_PUSH_SCHEMA_VERSION:
        raise ValueError("unsupported push preview schema")
    _validate_preview_id(preview.id)
    for value in (preview.repository_root, preview.worktree_root):
        if not value or len(value) > 4096 or "\x00" in value:
            raise ValueError("push preview path is invalid")
    HostedRepositoryHint(preview.repository_host, preview.repository_name)
    _validate_ref_component(preview.branch, "branch")
    _validate_ref_component(preview.remote, "remote")
    _validate_ref_component(preview.target_branch, "target branch")
    if preview.remote_ref != f"refs/heads/{preview.target_branch}":
        raise ValueError("push preview remote ref is invalid")
    if _HEX_SHA_RE.fullmatch(preview.head) is None:
        raise ValueError("push preview head is invalid")
    if _HEX_SHA_RE.fullmatch(preview.diff_sha256) is None:
        raise ValueError("push preview diff hash is invalid")
    if (
        preview.remote_head is not None
        and _HEX_SHA_RE.fullmatch(preview.remote_head) is None
    ):
        raise ValueError("push preview remote head is invalid")
    if preview.ahead < 0 or preview.behind < 0:
        raise ValueError("push preview divergence is invalid")
    if preview.set_upstream != (preview.upstream is None):
        raise ValueError("push preview upstream permission is invalid")
    _parse_timestamp(preview.created_at)


def _validate_result(result: EnvironmentPushResult) -> None:
    if result.schema_version != ENVIRONMENT_PUSH_SCHEMA_VERSION:
        raise ValueError("unsupported push result schema")
    if _PREVIEW_ID_RE.fullmatch(result.preview_id) is None:
        raise ValueError("push result preview id is invalid")
    _validate_ref_component(result.remote, "remote")
    _validate_ref_component(result.branch, "branch")
    _validate_ref_component(result.target_branch, "target branch")
    if result.remote_ref != f"refs/heads/{result.target_branch}":
        raise ValueError("push result remote ref is invalid")
    if _HEX_SHA_RE.fullmatch(result.commit_head) is None:
        raise ValueError("push result commit is invalid")
    expected_suffix = f"/commit/{result.commit_head}"
    if (
        not result.remote_commit_url.startswith("https://")
        or not result.remote_commit_url.endswith(expected_suffix)
        or result.run_evidence_url != f"{result.remote_commit_url}/checks"
    ):
        raise ValueError("push result evidence links are invalid")
    _parse_timestamp(result.completed_at)


def _validate_preview_id(value: str) -> None:
    if _PREVIEW_ID_RE.fullmatch(str(value)) is None:
        raise EnvironmentPushError("preview_invalid", "Push preview is invalid.")


def _validate_ref_component(value: str, field: str) -> None:
    text = str(value)
    if (
        not text
        or len(text) > 512
        or text.startswith("/")
        or text.endswith(("/", ".", ".lock"))
        or "//" in text
        or _REF_COMPONENT_RE.fullmatch(text) is None
    ):
        raise EnvironmentPushError("ref_invalid", f"Git {field} is invalid.")


def _classify_push_failure(payload: bytes) -> str:
    """Classify bounded Git diagnostics without retaining their content."""
    text = payload[:8192].decode("utf-8", "replace").casefold()
    if "protected branch" in text or "gh006" in text:
        return "protected_branch"
    if any(
        marker in text
        for marker in (
            "permission",
            "denied",
            "forbidden",
            "not authorized",
            "authentication failed",
        )
    ):
        return "permission_denied"
    if any(
        marker in text
        for marker in ("could not resolve", "network", "connection", "timed out")
    ):
        return "network_unavailable"
    return "push_failed"


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
