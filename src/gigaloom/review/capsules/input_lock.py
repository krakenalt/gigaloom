"""Canonical input-lock construction and validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .canonical import (
    GIT_REVISION_RE,
    canonical_json_bytes,
    canonical_sha256,
    require_bool,
    require_exact_fields,
    require_hash_list,
    require_identity,
    require_list,
    require_mapping,
    require_non_negative_int,
    require_omission_list,
    require_optional_sha256,
    require_sha256,
    require_string,
    validate_content_free,
)
from .errors import CapsuleIntegrityError, CapsuleSchemaError
from .models import INPUT_LOCK_KIND, RUN_CAPSULE_SCHEMA_VERSION, InputLock

_PAYLOAD_FIELDS = {
    "project",
    "git",
    "context_manifest",
    "route_decision",
    "agent_profile",
    "structured_route_id",
    "executable",
    "acp",
    "launch_profile_sha256",
    "model_account",
    "environment",
    "extensions",
    "governance",
    "attachments",
    "time",
    "omissions",
}
_DOCUMENT_FIELDS = _PAYLOAD_FIELDS | {
    "schema_version",
    "kind",
    "content_free",
    "input_lock_sha256",
}


def build_input_lock(payload: Mapping[str, Any]) -> InputLock:
    """Build a strict content-addressed input lock from content-free facts."""
    data = dict(require_mapping(payload, "input lock payload"))
    require_exact_fields(data, "input lock payload", _PAYLOAD_FIELDS)
    document = {
        "schema_version": RUN_CAPSULE_SCHEMA_VERSION,
        "kind": INPUT_LOCK_KIND,
        "content_free": True,
        **data,
    }
    _validate_input_lock(document, with_digest=False)
    document["input_lock_sha256"] = canonical_sha256(document)
    return InputLock(canonical_json_bytes(document))


def parse_input_lock(payload: object) -> InputLock:
    """Parse and verify one exact schema-v1 input lock."""
    document = dict(require_mapping(payload, "input lock"))
    _validate_input_lock(document, with_digest=True)
    expected = document["input_lock_sha256"]
    body = {key: value for key, value in document.items() if key != "input_lock_sha256"}
    if canonical_sha256(body) != expected:
        raise CapsuleIntegrityError("input lock digest does not match")
    return InputLock(canonical_json_bytes(document))


def _validate_input_lock(document: Mapping[str, Any], *, with_digest: bool) -> None:
    expected_fields = (
        _DOCUMENT_FIELDS if with_digest else _DOCUMENT_FIELDS - {"input_lock_sha256"}
    )
    require_exact_fields(document, "input lock", expected_fields)
    if document.get("schema_version") != RUN_CAPSULE_SCHEMA_VERSION:
        raise CapsuleSchemaError("unsupported input lock schema_version")
    if (
        document.get("kind") != INPUT_LOCK_KIND
        or document.get("content_free") is not True
    ):
        raise CapsuleSchemaError("input lock identity or content policy is invalid")
    if with_digest:
        require_sha256(document.get("input_lock_sha256"), "input_lock_sha256")
    validate_content_free(document)
    _validate_project(document.get("project"))
    _validate_git(document.get("git"))
    _validate_binding(document.get("context_manifest"), "context_manifest")
    _validate_binding(document.get("route_decision"), "route_decision")
    _validate_agent_profile(document.get("agent_profile"))
    require_identity(document.get("structured_route_id"), "structured_route_id")
    _validate_executable(document.get("executable"))
    _validate_acp(document.get("acp"))
    require_optional_sha256(
        document.get("launch_profile_sha256"), "launch_profile_sha256"
    )
    _validate_model_account(document.get("model_account"))
    _validate_digest_group(
        document.get("environment"),
        "environment",
        {"python", "os", "toolchain", "container"},
    )
    _validate_extensions(document.get("extensions"))
    _validate_digest_group(
        document.get("governance"),
        "governance",
        {"policy", "authority", "source_to_sink", "network"},
    )
    _validate_attachments(document.get("attachments"))
    _validate_time(document.get("time"))
    require_omission_list(document.get("omissions"), "omissions")


def _validate_project(value: Any) -> None:
    project = require_mapping(value, "project")
    require_exact_fields(
        project, "project", {"catalog_id", "catalog_sha256", "workspace_ref_sha256"}
    )
    require_identity(project.get("catalog_id"), "project.catalog_id")
    require_sha256(project.get("catalog_sha256"), "project.catalog_sha256")
    require_sha256(project.get("workspace_ref_sha256"), "project.workspace_ref_sha256")


def _validate_git(value: Any) -> None:
    git = require_mapping(value, "git")
    require_exact_fields(git, "git", {"base_commit", "dirty", "files"})
    revision = require_string(git.get("base_commit"), "git.base_commit", max_length=64)
    if GIT_REVISION_RE.fullmatch(revision) is None:
        raise CapsuleSchemaError("git.base_commit is invalid")
    require_bool(git.get("dirty"), "git.dirty")
    files = require_list(git.get("files"), "git.files", limit=256)
    paths: list[str] = []
    for index, item in enumerate(files):
        file = require_mapping(item, f"git.files[{index}]")
        require_exact_fields(
            file, f"git.files[{index}]", {"path", "sha256", "byte_count"}
        )
        path = require_string(
            file.get("path"), f"git.files[{index}].path", max_length=512
        )
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or path in {".", ""}:
            raise CapsuleSchemaError(f"git.files[{index}].path is unsafe")
        paths.append(path)
        require_sha256(file.get("sha256"), f"git.files[{index}].sha256")
        require_non_negative_int(
            file.get("byte_count"), f"git.files[{index}].byte_count"
        )
    if paths != sorted(set(paths)):
        raise CapsuleSchemaError("git.files paths must be sorted and unique")


def _validate_binding(value: Any, field: str) -> None:
    binding = require_mapping(value, field)
    require_exact_fields(binding, field, {"id", "sha256"})
    require_identity(binding.get("id"), f"{field}.id")
    require_sha256(binding.get("sha256"), f"{field}.sha256")


def _validate_agent_profile(value: Any) -> None:
    profile = require_mapping(value, "agent_profile")
    require_exact_fields(profile, "agent_profile", {"id", "version", "sha256"})
    require_identity(profile.get("id"), "agent_profile.id")
    require_identity(profile.get("version"), "agent_profile.version")
    require_sha256(profile.get("sha256"), "agent_profile.sha256")


def _validate_executable(value: Any) -> None:
    executable = require_mapping(value, "executable")
    require_exact_fields(executable, "executable", {"path_sha256", "version", "sha256"})
    require_sha256(executable.get("path_sha256"), "executable.path_sha256")
    require_string(executable.get("version"), "executable.version")
    require_sha256(executable.get("sha256"), "executable.sha256")


def _validate_acp(value: Any) -> None:
    if value is None:
        return
    acp = require_mapping(value, "acp")
    require_exact_fields(
        acp, "acp", {"protocol_version", "profile_sha256", "capabilities_sha256"}
    )
    require_identity(acp.get("protocol_version"), "acp.protocol_version")
    require_sha256(acp.get("profile_sha256"), "acp.profile_sha256")
    require_sha256(acp.get("capabilities_sha256"), "acp.capabilities_sha256")


def _validate_model_account(value: Any) -> None:
    model = require_mapping(value, "model_account")
    require_exact_fields(model, "model_account", {"model_id", "account_id"})
    for key in ("model_id", "account_id"):
        if model.get(key) is not None:
            require_identity(model.get(key), f"model_account.{key}")


def _validate_digest_group(value: Any, field: str, keys: set[str]) -> None:
    group = require_mapping(value, field)
    require_exact_fields(group, field, keys)
    for key in keys:
        require_optional_sha256(group.get(key), f"{field}.{key}")


def _validate_extensions(value: Any) -> None:
    extensions = require_mapping(value, "extensions")
    require_exact_fields(extensions, "extensions", {"skills", "plugins", "mcp"})
    for key in ("skills", "plugins", "mcp"):
        require_hash_list(extensions.get(key), f"extensions.{key}")


def _validate_attachments(value: Any) -> None:
    attachments = require_list(value, "attachments", limit=128)
    identities: list[str] = []
    for index, item in enumerate(attachments):
        attachment = require_mapping(item, f"attachments[{index}]")
        require_exact_fields(
            attachment,
            f"attachments[{index}]",
            {"ref_id", "sha256", "status", "byte_count"},
        )
        identities.append(
            require_identity(attachment.get("ref_id"), f"attachments[{index}].ref_id")
        )
        require_optional_sha256(
            attachment.get("sha256"), f"attachments[{index}].sha256"
        )
        require_identity(attachment.get("status"), f"attachments[{index}].status")
        if attachment.get("byte_count") is not None:
            require_non_negative_int(
                attachment.get("byte_count"), f"attachments[{index}].byte_count"
            )
    if identities != sorted(set(identities)):
        raise CapsuleSchemaError("attachments must be sorted and unique by ref_id")


def _validate_time(value: Any) -> None:
    evidence = require_mapping(value, "time")
    require_exact_fields(evidence, "time", {"source", "captured_at", "clock_sha256"})
    require_identity(evidence.get("source"), "time.source")
    require_string(evidence.get("captured_at"), "time.captured_at")
    require_sha256(evidence.get("clock_sha256"), "time.clock_sha256")
