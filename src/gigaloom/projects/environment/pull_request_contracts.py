"""Immutable contracts and validation for environment pull request."""

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
from urllib.parse import urlsplit

from .models import EnvironmentSnapshot, HostedRepositoryHint

ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION = 1


ENVIRONMENT_PULL_REQUEST_OWNER = "environment.pull_request"


MAX_PULL_REQUEST_TITLE_CHARS = 256


MAX_PULL_REQUEST_BODY_CHARS = 16 * 1024


MAX_HOSTED_OUTPUT_BYTES = 1024 * 1024


HOSTED_COMMAND_TIMEOUT_SECONDS = 15.0


_HEX_SHA_RE = re.compile(r"[0-9a-f]{40,64}\Z")


_PREVIEW_ID_RE = re.compile(r"pull_request_[0-9a-f]{64}\Z")


_REF_COMPONENT_RE = re.compile(r"(?!-)(?!.*\.\.)(?!.*@\{)[^\x00-\x20~^:?*\\]+\Z")


class EnvironmentPullRequestError(RuntimeError):
    """Content-free failure for one governed hosted pull-request action."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EnvironmentPullRequestPreview:
    """Private preview of one exact hosted pull-request creation intent."""

    id: str
    repository_root: str
    worktree_root: str
    repository_host: str
    repository_name: str
    repository_url: str
    remote: str
    source_branch: str
    source_head: str
    source_remote_head: str
    base_branch: str
    base_head: str
    diff_sha256: str
    title: str
    body: str
    created_at: str
    schema_version: int = ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION

    @property
    def approval_binding(self) -> str:
        """Return the opaque immutable binding consumed by the policy owner."""
        return f"environment-pull-request-v1:{self.id}"

    @property
    def scope_id(self) -> str:
        """Return a content-free project scope for allow-once approvals."""
        digest = hashlib.sha256(self.worktree_root.encode("utf-8")).hexdigest()
        return f"environment_{digest[:32]}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the exact hosted-write preview."""
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "repository_root": self.repository_root,
            "worktree_root": self.worktree_root,
            "repository": {
                "host": self.repository_host,
                "name_with_owner": self.repository_name,
                "url": self.repository_url,
            },
            "remote": self.remote,
            "source_branch": self.source_branch,
            "source_head": self.source_head,
            "source_remote_head": self.source_remote_head,
            "base_branch": self.base_branch,
            "base_head": self.base_head,
            "diff_sha256": self.diff_sha256,
            "title": self.title,
            "body": self.body,
            "permissions": {
                "network_connect": True,
                "hosted_write": True,
                "create_pull_request": True,
                "update_pull_request": False,
                "merge_pull_request": False,
                "write_issue": False,
                "write_checks": False,
                "write_actions": False,
                "push_commits": False,
            },
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentPullRequestPreview:
        """Parse one strict private preview record."""
        repository = payload.get("repository")
        if not isinstance(repository, Mapping):
            raise ValueError("pull-request preview repository is invalid")
        preview = cls(
            schema_version=int(payload.get("schema_version", 0)),
            id=str(payload.get("id", "")),
            repository_root=str(payload.get("repository_root", "")),
            worktree_root=str(payload.get("worktree_root", "")),
            repository_host=str(repository.get("host", "")),
            repository_name=str(repository.get("name_with_owner", "")),
            repository_url=str(repository.get("url", "")),
            remote=str(payload.get("remote", "")),
            source_branch=str(payload.get("source_branch", "")),
            source_head=str(payload.get("source_head", "")),
            source_remote_head=str(payload.get("source_remote_head", "")),
            base_branch=str(payload.get("base_branch", "")),
            base_head=str(payload.get("base_head", "")),
            diff_sha256=str(payload.get("diff_sha256", "")),
            title=str(payload.get("title", "")),
            body=str(payload.get("body", "")),
            created_at=str(payload.get("created_at", "")),
        )
        _validate_preview(preview)
        return preview


@dataclass(frozen=True)
class EnvironmentPullRequestResult:
    """Durable, bounded evidence for one exact pull request."""

    preview_id: str
    number: int
    state: str
    source_branch: str
    base_branch: str
    commit_head: str
    pull_request_url: str
    commit_url: str
    checks_url: str
    run_evidence_url: str
    completed_at: str
    recovered: bool = False
    schema_version: int = ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialize bounded hosted completion evidence."""
        return {
            "schema_version": self.schema_version,
            "preview_id": self.preview_id,
            "number": self.number,
            "state": self.state,
            "source_branch": self.source_branch,
            "base_branch": self.base_branch,
            "commit_head": self.commit_head,
            "pull_request_url": self.pull_request_url,
            "commit_url": self.commit_url,
            "checks_url": self.checks_url,
            "run_evidence_url": self.run_evidence_url,
            "completed_at": self.completed_at,
            "recovered": self.recovered,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentPullRequestResult:
        """Parse one strict durable completion record."""
        result = cls(
            schema_version=int(payload.get("schema_version", 0)),
            preview_id=str(payload.get("preview_id", "")),
            number=int(payload.get("number", 0)),
            state=str(payload.get("state", "")),
            source_branch=str(payload.get("source_branch", "")),
            base_branch=str(payload.get("base_branch", "")),
            commit_head=str(payload.get("commit_head", "")),
            pull_request_url=str(payload.get("pull_request_url", "")),
            commit_url=str(payload.get("commit_url", "")),
            checks_url=str(payload.get("checks_url", "")),
            run_evidence_url=str(payload.get("run_evidence_url", "")),
            completed_at=str(payload.get("completed_at", "")),
            recovered=bool(payload.get("recovered", False)),
        )
        _validate_result(result)
        return result


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


HostedCommandRunner = Callable[
    [tuple[str, ...], Path, bytes | None, float], _CommandResult
]


def _parse_pull_request(
    payload: bytes, preview: EnvironmentPullRequestPreview
) -> Mapping[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvironmentPullRequestError(
            "hosted_output_invalid", "Pull-request result is invalid."
        ) from exc
    if not isinstance(value, Mapping):
        raise EnvironmentPullRequestError(
            "hosted_output_invalid", "Pull-request result is invalid."
        )
    _parse_pull_request_mapping(value, preview)
    return value


def _parse_pull_request_mapping(
    value: Mapping[str, Any], preview: EnvironmentPullRequestPreview
) -> dict[str, Any]:
    number = value.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise EnvironmentPullRequestError(
            "hosted_output_invalid", "Pull-request number is invalid."
        )
    url = value.get("html_url", value.get("url"))
    if not isinstance(url, str):
        raise EnvironmentPullRequestError(
            "hosted_output_invalid", "Pull-request URL is invalid."
        )
    expected = f"{preview.repository_url}/pull/{number}"
    if url != expected:
        raise EnvironmentPullRequestError(
            "repository_mismatch", "Pull-request repository identity changed."
        )
    state = str(value.get("state", "")).casefold()
    if state != "open":
        raise EnvironmentPullRequestError(
            "hosted_output_invalid", "Pull-request state is invalid."
        )
    head_ref = value.get("headRefName")
    head_oid = value.get("headRefOid")
    base_ref = value.get("baseRefName")
    if isinstance(value.get("head"), Mapping):
        head_ref = value["head"].get("ref")
        head_oid = value["head"].get("sha")
    if isinstance(value.get("base"), Mapping):
        base_ref = value["base"].get("ref")
    if (
        head_ref != preview.source_branch
        or head_oid != preview.source_head
        or base_ref != preview.base_branch
    ):
        raise EnvironmentPullRequestError(
            "pull_request_mismatch", "Pull-request identity changed."
        )
    return {"number": number, "url": url, "state": state}


def _snapshot_matches_preview(
    snapshot: EnvironmentSnapshot, preview: EnvironmentPullRequestPreview
) -> bool:
    return (
        snapshot.repository_root == preview.repository_root
        and snapshot.worktree_root == preview.worktree_root
        and snapshot.branch is not None
        and snapshot.head == preview.source_head
        and snapshot.diff_sha256 == preview.diff_sha256
        and snapshot.remote == preview.remote
        and not snapshot.detached
        and (
            snapshot.branch == preview.source_branch
            or snapshot.upstream == f"{preview.remote}/{preview.source_branch}"
        )
    )


def _validate_preview(preview: EnvironmentPullRequestPreview) -> None:
    if preview.schema_version != ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION:
        raise ValueError("unsupported pull-request preview schema")
    _validate_preview_id(preview.id)
    for value in (preview.repository_root, preview.worktree_root):
        if not value or len(value) > 4096 or "\x00" in value:
            raise ValueError("pull-request preview path is invalid")
    hint = HostedRepositoryHint(preview.repository_host, preview.repository_name)
    if _safe_repository_url(preview.repository_url, hint) != preview.repository_url:
        raise ValueError("pull-request repository URL is invalid")
    _validate_ref_component(preview.remote, "remote")
    _validate_ref_component(preview.source_branch, "source branch")
    _validate_ref_component(preview.base_branch, "base branch")
    for value in (
        preview.source_head,
        preview.source_remote_head,
        preview.base_head,
        preview.diff_sha256,
    ):
        if _HEX_SHA_RE.fullmatch(value) is None:
            raise ValueError("pull-request preview hash is invalid")
    _validate_title(preview.title)
    _validate_body(preview.body)
    _parse_timestamp(preview.created_at)


def _validate_result(result: EnvironmentPullRequestResult) -> None:
    if result.schema_version != ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION:
        raise ValueError("unsupported pull-request result schema")
    if _PREVIEW_ID_RE.fullmatch(result.preview_id) is None:
        raise ValueError("pull-request result preview id is invalid")
    if result.number <= 0 or result.state != "open":
        raise ValueError("pull-request result identity is invalid")
    _validate_ref_component(result.source_branch, "source branch")
    _validate_ref_component(result.base_branch, "base branch")
    if _HEX_SHA_RE.fullmatch(result.commit_head) is None:
        raise ValueError("pull-request result commit is invalid")
    parsed = urlsplit(result.pull_request_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(f"/pull/{result.number}")
    ):
        raise ValueError("pull-request result URL is invalid")
    repository_url = result.pull_request_url.removesuffix(f"/pull/{result.number}")
    if result.commit_url != f"{repository_url}/commit/{result.commit_head}":
        raise ValueError("pull-request commit URL is invalid")
    if result.checks_url != f"{result.pull_request_url}/checks":
        raise ValueError("pull-request checks URL is invalid")
    if not result.run_evidence_url.startswith(
        f"{repository_url}/actions?query=branch%3A"
    ):
        raise ValueError("pull-request run evidence URL is invalid")
    _parse_timestamp(result.completed_at)


def _validate_title(value: str) -> str:
    text = str(value).strip()
    if (
        not text
        or len(text) > MAX_PULL_REQUEST_TITLE_CHARS
        or any(ord(char) < 32 for char in text)
    ):
        raise EnvironmentPullRequestError(
            "title_invalid", "Pull-request title is invalid."
        )
    return text


def _validate_body(value: str) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > MAX_PULL_REQUEST_BODY_CHARS or "\x00" in text:
        raise EnvironmentPullRequestError(
            "body_invalid", "Pull-request body is invalid."
        )
    return text


def _validate_preview_id(value: str) -> None:
    if _PREVIEW_ID_RE.fullmatch(str(value)) is None:
        raise EnvironmentPullRequestError(
            "preview_invalid", "Pull-request preview is invalid."
        )


def _validate_ref_component(value: str, field: str) -> str:
    text = str(value)
    if (
        not text
        or len(text) > 512
        or text.startswith("/")
        or text.endswith(("/", ".", ".lock"))
        or "//" in text
        or _REF_COMPONENT_RE.fullmatch(text) is None
    ):
        raise EnvironmentPullRequestError("ref_invalid", f"Git {field} is invalid.")
    return text


def _safe_repository_url(value: str, hint: HostedRepositoryHint) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise EnvironmentPullRequestError(
            "hosted_output_invalid", "Hosted repository URL is invalid."
        ) from exc
    if parsed.hostname is not None and (
        parsed.hostname.casefold() != hint.host.casefold()
    ):
        raise EnvironmentPullRequestError(
            "host_mismatch", "Hosted repository URL uses an unexpected host."
        )
    expected_path = f"/{hint.name_with_owner}"
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/").casefold() != expected_path.casefold()
    ):
        raise EnvironmentPullRequestError(
            "hosted_output_invalid", "Hosted repository URL is invalid."
        )
    return value.rstrip("/")


def _classify_hosted_failure(payload: bytes, *, default: str = "hosted_failed") -> str:
    text = payload[:8192].decode("utf-8", "replace").casefold()
    if "rate limit" in text or "secondary rate" in text:
        return "rate_limited"
    if "not logged" in text or "authentication" in text or "bad credentials" in text:
        return "unauthenticated"
    if "permission" in text or "forbidden" in text or "resource not accessible" in text:
        return "permission_denied"
    if "could not resolve" in text or "network" in text or "connection" in text:
        return "network_unavailable"
    return default


def _mapping_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
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
