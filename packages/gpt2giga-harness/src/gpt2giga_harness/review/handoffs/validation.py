"""Review validation primitives."""

from __future__ import annotations

import json
from typing import Any, Mapping
from .codec import (
    _exact_fields,
    _exact_mapping,
    _hash_list,
    _json_hash,
    _mapping,
    _non_negative_integer,
    _object_list,
    _required_hash,
    _required_identity,
)
from .models import (
    HANDOFF_CAPSULE_SCHEMA_VERSION,
    HandoffCapsuleError,
    MAX_CAPSULE_ARTIFACTS,
    MAX_CAPSULE_QUESTIONS,
)


def verify_handoff_capsule(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the exact schema, semantic hash, and truthful continuity claim."""
    if not isinstance(payload, Mapping):
        raise HandoffCapsuleError("handoff capsule must be an object")
    expected = {
        "schema_version",
        "kind",
        "content_free",
        "capsule_id",
        "capsule_sha256",
        "summary",
        "diff_and_artifacts",
        "tool_extension_snapshot",
        "provenance",
        "unresolved",
        "environment",
        "continuity",
    }
    if set(payload) != expected:
        raise HandoffCapsuleError("handoff capsule fields are invalid")
    if payload.get("schema_version") != HANDOFF_CAPSULE_SCHEMA_VERSION:
        raise HandoffCapsuleError("unsupported handoff capsule schema_version")
    if payload.get("kind") != "agent_workbench.handoff_capsule.v1":
        raise HandoffCapsuleError("handoff capsule kind is invalid")
    if payload.get("content_free") is not True:
        raise HandoffCapsuleError("handoff capsule must be content-free")
    capsule_sha256 = _required_hash(payload.get("capsule_sha256"), "capsule_sha256")
    if payload.get("capsule_id") != f"handoff_{capsule_sha256[:32]}":
        raise HandoffCapsuleError("handoff capsule id does not match")
    continuity = _mapping(payload.get("continuity"), "continuity")
    _validate_capsule_shape(payload)
    for key in (
        "native_session_identity_preserved",
        "provider_session_identity_preserved",
        "harness_session_identity_preserved",
    ):
        if continuity.get(key) is not False:
            raise HandoffCapsuleError(
                "handoff capsule cannot preserve cross-Harness session identity"
            )
    provenance = _mapping(payload.get("provenance"), "provenance")
    source = _mapping(provenance.get("source"), "provenance.source")
    target = _mapping(provenance.get("target"), "provenance.target")
    if source.get("harness_id") == target.get("harness_id"):
        raise HandoffCapsuleError("handoff capsule target must differ from source")
    body = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "capsule_id",
            "capsule_sha256",
        }
    }
    if _json_hash(body) != capsule_sha256:
        raise HandoffCapsuleError("handoff capsule hash does not match")
    return json.loads(json.dumps(payload))


def _validate_capsule_shape(payload: Mapping[str, Any]) -> None:
    summary = _exact_mapping(
        payload.get("summary"),
        "summary",
        {
            "source_status",
            "mode",
            "capability",
            "invocation_mode",
            "task_sha256",
            "artifact_count",
            "pending_approval_count",
            "unresolved_question_count",
        },
    )
    _required_hash(summary.get("task_sha256"), "summary.task_sha256")
    for key in (
        "artifact_count",
        "pending_approval_count",
        "unresolved_question_count",
    ):
        _non_negative_integer(summary.get(key), f"summary.{key}")
    diff_and_artifacts = _exact_mapping(
        payload.get("diff_and_artifacts"),
        "diff_and_artifacts",
        {"diff", "artifacts"},
    )
    diff = _exact_mapping(
        diff_and_artifacts.get("diff"),
        "diff_and_artifacts.diff",
        {
            "sha256",
            "byte_count",
            "captured",
            "truncated",
            "changed_file_count",
            "untracked_file_count",
            "path_identities",
        },
    )
    _required_hash(diff.get("sha256"), "diff_and_artifacts.diff.sha256")
    for key in ("byte_count", "changed_file_count", "untracked_file_count"):
        _non_negative_integer(diff.get(key), f"diff_and_artifacts.diff.{key}")
    for key in ("captured", "truncated"):
        if not isinstance(diff.get(key), bool):
            raise HandoffCapsuleError(
                f"handoff capsule diff_and_artifacts.diff.{key} must be a boolean"
            )
    _hash_list(diff.get("path_identities"), "diff_and_artifacts.diff.path_identities")
    artifacts = _object_list(
        diff_and_artifacts.get("artifacts"),
        "diff_and_artifacts.artifacts",
        limit=MAX_CAPSULE_ARTIFACTS,
    )
    for index, artifact in enumerate(artifacts):
        _exact_fields(
            artifact,
            f"diff_and_artifacts.artifacts[{index}]",
            {"type", "identity_sha256", "byte_count"},
        )
        _required_hash(
            artifact.get("identity_sha256"),
            f"diff_and_artifacts.artifacts[{index}].identity_sha256",
        )
        if artifact.get("byte_count") is not None:
            _non_negative_integer(
                artifact.get("byte_count"),
                f"diff_and_artifacts.artifacts[{index}].byte_count",
            )
    if summary["artifact_count"] != len(artifacts):
        raise HandoffCapsuleError("handoff capsule artifact count does not match")
    tools = _exact_mapping(
        payload.get("tool_extension_snapshot"),
        "tool_extension_snapshot",
        {
            "managed_mcp_snapshot_id",
            "snapshot_sha256",
            "server_count",
            "server_identities",
            "descriptor_contents_included",
            "secret_values_included",
            "projection_sha256",
        },
    )
    if tools.get("snapshot_sha256") is not None:
        _required_hash(
            tools.get("snapshot_sha256"),
            "tool_extension_snapshot.snapshot_sha256",
        )
    server_ids = _hash_list(
        tools.get("server_identities"),
        "tool_extension_snapshot.server_identities",
    )
    if _non_negative_integer(
        tools.get("server_count"), "tool_extension_snapshot.server_count"
    ) != len(server_ids):
        raise HandoffCapsuleError("handoff capsule server count does not match")
    if (
        tools.get("descriptor_contents_included") is not False
        or tools.get("secret_values_included") is not False
    ):
        raise HandoffCapsuleError("handoff capsule tool snapshot is not content-free")
    _required_hash(
        tools.get("projection_sha256"),
        "tool_extension_snapshot.projection_sha256",
    )
    unresolved = _exact_mapping(
        payload.get("unresolved"),
        "unresolved",
        {"approvals", "questions", "approval_source"},
    )
    approvals = _object_list(
        unresolved.get("approvals"),
        "unresolved.approvals",
        limit=MAX_CAPSULE_ARTIFACTS,
    )
    questions = _object_list(
        unresolved.get("questions"),
        "unresolved.questions",
        limit=MAX_CAPSULE_QUESTIONS,
    )
    if summary["pending_approval_count"] != len(approvals):
        raise HandoffCapsuleError("handoff capsule approval count does not match")
    if summary["unresolved_question_count"] != len(questions):
        raise HandoffCapsuleError("handoff capsule question count does not match")
    for index, question in enumerate(questions):
        _exact_fields(
            question,
            f"unresolved.questions[{index}]",
            {"id", "type", "created_at", "content_included"},
        )
        _required_hash(question.get("id"), f"unresolved.questions[{index}].id")
        if question.get("content_included") is not False:
            raise HandoffCapsuleError("handoff capsule question contains content")
    environment = _exact_mapping(
        payload.get("environment"),
        "environment",
        {
            "provider_id",
            "workspace_sha256",
            "branch",
            "detached",
            "head",
            "base_identity",
            "upstream",
            "ahead",
            "behind",
            "diff_sha256",
            "changed_paths",
            "changed_paths_truncated",
            "snapshot_sha256",
        },
    )
    for key in ("workspace_sha256", "diff_sha256", "snapshot_sha256"):
        _required_hash(environment.get(key), f"environment.{key}")
    _non_negative_integer(environment.get("ahead"), "environment.ahead")
    _non_negative_integer(environment.get("behind"), "environment.behind")
    if not isinstance(environment.get("detached"), bool) or not isinstance(
        environment.get("changed_paths_truncated"), bool
    ):
        raise HandoffCapsuleError("handoff capsule environment flags are invalid")
    if not isinstance(environment.get("changed_paths"), list) or any(
        not isinstance(item, str) for item in environment["changed_paths"]
    ):
        raise HandoffCapsuleError("handoff capsule environment paths are invalid")
    provenance = _mapping(payload.get("provenance"), "provenance")
    source = _exact_mapping(
        provenance.get("source"),
        "provenance.source",
        {
            "run_id",
            "session_id",
            "harness_id",
            "harness_contract_sha256",
            "provider",
        },
    )
    target = _exact_mapping(
        provenance.get("target"),
        "provenance.target",
        {"harness_id", "harness_contract_sha256", "session_requirement"},
    )
    for key in ("run_id", "session_id", "harness_id"):
        _required_identity(source.get(key), f"provenance.source.{key}")
    _required_identity(target.get("harness_id"), "provenance.target.harness_id")
    _required_hash(
        source.get("harness_contract_sha256"),
        "provenance.source.harness_contract_sha256",
    )
    _required_hash(
        target.get("harness_contract_sha256"),
        "provenance.target.harness_contract_sha256",
    )
    if target.get("session_requirement") != "new_or_explicit_import":
        raise HandoffCapsuleError(
            "handoff capsule target session requirement is invalid"
        )
    continuity = _exact_mapping(
        payload.get("continuity"),
        "continuity",
        {
            "native_session_identity_preserved",
            "provider_session_identity_preserved",
            "harness_session_identity_preserved",
            "source_native_session_present",
            "claim",
        },
    )
    if not isinstance(continuity.get("source_native_session_present"), bool):
        raise HandoffCapsuleError("handoff capsule native session presence is invalid")
    if continuity.get("claim") != "evidence_handoff_only":
        raise HandoffCapsuleError("handoff capsule continuity claim is invalid")
