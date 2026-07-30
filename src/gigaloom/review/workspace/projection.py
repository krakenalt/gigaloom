"""Canonical construction and verification for Operator Evidence Workspace."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping, cast

from .models import (
    EVIDENCE_WORKSPACE_KIND,
    EVIDENCE_WORKSPACE_SCHEMA_VERSION,
    MAX_CHANGED_FILES,
    MAX_EVIDENCE_REFERENCES,
    MAX_NEXT_ACTIONS,
    ChangeSetProjection,
    EvidenceFreshness,
    EvidenceOmission,
    EvidenceOmissionReason,
    EvidenceReference,
    EvidenceSection,
    EvidenceWorkspaceProjection,
    EvidenceWorkspaceRun,
    NextActionReference,
)


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@~-]{0,255}\Z")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_SECTIONS = frozenset(EvidenceSection)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "run",
        "references",
        "change_set",
        "omissions",
        "staleness",
        "next_actions",
        "projection_sha256",
    }
)


def build_evidence_workspace(
    *,
    run: EvidenceWorkspaceRun,
    references: Iterable[EvidenceReference] = (),
    change_set: ChangeSetProjection | None = None,
    omissions: Iterable[EvidenceOmission] = (),
    next_actions: Iterable[NextActionReference] = (),
) -> EvidenceWorkspaceProjection:
    """Build one bounded projection without reading or copying owner payloads."""
    checked_run = _validate_run(run)
    checked_references = tuple(_validate_reference(item) for item in references)
    checked_omissions = tuple(_validate_omission(item) for item in omissions)
    checked_actions = tuple(_validate_action(item) for item in next_actions)
    checked_change_set = _validate_change_set(change_set) if change_set else None

    if len(checked_references) > MAX_EVIDENCE_REFERENCES:
        raise ValueError("evidence reference limit exceeded")
    if len(checked_actions) > MAX_NEXT_ACTIONS:
        raise ValueError("next action limit exceeded")

    _validate_bindings(
        checked_run,
        checked_references,
        checked_change_set,
        checked_actions,
    )
    _validate_coverage(checked_references, checked_change_set, checked_omissions)
    _reject_duplicates(checked_references, checked_omissions, checked_actions)

    ordered_references = tuple(
        sorted(
            checked_references,
            key=lambda item: (
                item.section.value,
                item.kind,
                item.authority,
                item.resource_id,
            ),
        )
    )
    ordered_omissions = tuple(
        sorted(checked_omissions, key=lambda item: item.section.value)
    )
    ordered_actions = tuple(
        sorted(
            checked_actions,
            key=lambda item: (item.authority, item.kind, item.action_id),
        )
    )
    projection = EvidenceWorkspaceProjection(
        run=checked_run,
        references=ordered_references,
        change_set=checked_change_set,
        omissions=ordered_omissions,
        next_actions=ordered_actions,
        projection_sha256="",
    )
    body = _projection_body(projection)
    return EvidenceWorkspaceProjection(
        run=projection.run,
        references=projection.references,
        change_set=projection.change_set,
        omissions=projection.omissions,
        next_actions=projection.next_actions,
        projection_sha256=_json_hash(body),
    )


def evidence_workspace_to_dict(
    projection: EvidenceWorkspaceProjection,
) -> dict[str, object]:
    """Serialize one already verified projection."""
    body = _projection_body(projection)
    return {**body, "projection_sha256": projection.projection_sha256}


def evidence_workspace_from_dict(payload: object) -> EvidenceWorkspaceProjection:
    """Parse an exact schema-v1 document and verify its canonical digest."""
    data = _required_mapping(payload, "workspace")
    if frozenset(data) != _TOP_LEVEL_FIELDS:
        raise ValueError("workspace fields are invalid")
    if data.get("schema_version") != EVIDENCE_WORKSPACE_SCHEMA_VERSION:
        raise ValueError("unsupported evidence workspace schema_version")
    if data.get("kind") != EVIDENCE_WORKSPACE_KIND:
        raise ValueError("evidence workspace kind is invalid")

    run_data = _required_mapping(data.get("run"), "run")
    _require_fields(
        run_data,
        {"run_id", "session_id", "owner_id", "workspace_id", "status", "revision"},
        "run",
    )
    run = EvidenceWorkspaceRun(**{key: str(value) for key, value in run_data.items()})

    references = tuple(
        _reference_from_dict(item)
        for item in _required_list(data.get("references"), "references")
    )
    change_data = data.get("change_set")
    change_set = (
        None
        if change_data is None
        else _change_set_from_dict(_required_mapping(change_data, "change_set"))
    )
    omissions = tuple(
        _omission_from_dict(item)
        for item in _required_list(data.get("omissions"), "omissions")
    )
    actions = tuple(
        _action_from_dict(item)
        for item in _required_list(data.get("next_actions"), "next_actions")
    )
    rebuilt = build_evidence_workspace(
        run=run,
        references=references,
        change_set=change_set,
        omissions=omissions,
        next_actions=actions,
    )
    declared_digest = _required_sha256(
        data.get("projection_sha256"),
        "projection_sha256",
    )
    if rebuilt.projection_sha256 != declared_digest:
        raise ValueError("evidence workspace digest does not match")
    if evidence_workspace_to_dict(rebuilt).get("staleness") != data.get("staleness"):
        raise ValueError("evidence workspace staleness is not canonical")
    return rebuilt


def _projection_body(
    projection: EvidenceWorkspaceProjection,
) -> dict[str, object]:
    stale = [
        item
        for item in projection.references
        if item.freshness is EvidenceFreshness.STALE
    ]
    if (
        projection.change_set is not None
        and projection.change_set.freshness is EvidenceFreshness.STALE
    ):
        stale_sections = {EvidenceSection.CHANGE_SET.value}
    else:
        stale_sections = set()
    stale_sections.update(item.section.value for item in stale)
    return {
        "schema_version": EVIDENCE_WORKSPACE_SCHEMA_VERSION,
        "kind": EVIDENCE_WORKSPACE_KIND,
        "run": {
            "run_id": projection.run.run_id,
            "session_id": projection.run.session_id,
            "owner_id": projection.run.owner_id,
            "workspace_id": projection.run.workspace_id,
            "status": projection.run.status,
            "revision": projection.run.revision,
        },
        "references": [_reference_to_dict(item) for item in projection.references],
        "change_set": (
            _change_set_to_dict(projection.change_set)
            if projection.change_set is not None
            else None
        ),
        "omissions": [_omission_to_dict(item) for item in projection.omissions],
        "staleness": {
            "has_stale_evidence": bool(stale_sections),
            "stale_reference_count": len(stale)
            + bool(
                projection.change_set is not None
                and projection.change_set.freshness is EvidenceFreshness.STALE
            ),
            "sections": sorted(stale_sections),
        },
        "next_actions": [_action_to_dict(item) for item in projection.next_actions],
    }


def _validate_run(run: EvidenceWorkspaceRun) -> EvidenceWorkspaceRun:
    return EvidenceWorkspaceRun(
        run_id=_required_identity(run.run_id, "run_id"),
        session_id=_required_identity(run.session_id, "session_id"),
        owner_id=_required_identity(run.owner_id, "owner_id"),
        workspace_id=_required_identity(run.workspace_id, "workspace_id"),
        status=_required_token(run.status, "status"),
        revision=_required_identity(run.revision, "revision"),
    )


def _validate_reference(reference: EvidenceReference) -> EvidenceReference:
    section = EvidenceSection(reference.section)
    if section is EvidenceSection.CHANGE_SET:
        raise ValueError("change_set must use ChangeSetProjection")
    return EvidenceReference(
        section=section,
        kind=_required_token(reference.kind, "reference kind"),
        authority=_required_token(reference.authority, "reference authority"),
        resource_id=_required_identity(reference.resource_id, "resource_id"),
        owner_id=_required_identity(reference.owner_id, "owner_id"),
        workspace_id=_required_identity(reference.workspace_id, "workspace_id"),
        revision=_required_identity(reference.revision, "reference revision"),
        sha256=_required_sha256(reference.sha256, "reference sha256"),
        state=_required_token(reference.state, "reference state"),
        freshness=EvidenceFreshness(reference.freshness),
    )


def _validate_change_set(change_set: ChangeSetProjection) -> ChangeSetProjection:
    if len(change_set.changed_files) > MAX_CHANGED_FILES:
        raise ValueError("changed file limit exceeded")
    changed_files = tuple(
        sorted({_required_relative_path(item) for item in change_set.changed_files})
    )
    if len(changed_files) != len(change_set.changed_files):
        raise ValueError("changed files must be unique")
    return ChangeSetProjection(
        authority=_required_token(change_set.authority, "change_set authority"),
        owner_id=_required_identity(change_set.owner_id, "owner_id"),
        workspace_id=_required_identity(change_set.workspace_id, "workspace_id"),
        revision=_required_identity(change_set.revision, "change_set revision"),
        base_sha256=_required_sha256(change_set.base_sha256, "base_sha256"),
        patch_sha256=_required_sha256(change_set.patch_sha256, "patch_sha256"),
        changed_files=changed_files,
        truncated=bool(change_set.truncated),
        freshness=EvidenceFreshness(change_set.freshness),
    )


def _validate_omission(omission: EvidenceOmission) -> EvidenceOmission:
    return EvidenceOmission(
        section=EvidenceSection(omission.section),
        reason=EvidenceOmissionReason(omission.reason),
        authority=_required_token(omission.authority, "omission authority"),
    )


def _validate_action(action: NextActionReference) -> NextActionReference:
    expires_at = None
    if action.expires_at is not None:
        expires_at = str(action.expires_at).strip()
        if not expires_at or len(expires_at) > 64:
            raise ValueError("expires_at is invalid")
    return NextActionReference(
        action_id=_required_identity(action.action_id, "action_id"),
        kind=_required_token(action.kind, "action kind"),
        authority=_required_token(action.authority, "action authority"),
        owner_id=_required_identity(action.owner_id, "owner_id"),
        workspace_id=_required_identity(action.workspace_id, "workspace_id"),
        revision=_required_identity(action.revision, "action revision"),
        sha256=_required_sha256(action.sha256, "action sha256"),
        consequence=_required_token(action.consequence, "action consequence"),
        expires_at=expires_at,
    )


def _validate_bindings(
    run: EvidenceWorkspaceRun,
    references: tuple[EvidenceReference, ...],
    change_set: ChangeSetProjection | None,
    actions: tuple[NextActionReference, ...],
) -> None:
    bound = (*references, *(item for item in (change_set,) if item), *actions)
    for item in bound:
        if item.owner_id != run.owner_id:
            raise ValueError("evidence workspace owner binding does not match")
        if item.workspace_id != run.workspace_id:
            raise ValueError("evidence workspace binding does not match")


def _validate_coverage(
    references: tuple[EvidenceReference, ...],
    change_set: ChangeSetProjection | None,
    omissions: tuple[EvidenceOmission, ...],
) -> None:
    present = {item.section for item in references}
    if change_set is not None:
        present.add(EvidenceSection.CHANGE_SET)
    omitted = {item.section for item in omissions}
    overlap = present & omitted
    if overlap:
        raise ValueError(
            f"workspace sections cannot be present and omitted: {_section_names(overlap)}"
        )
    missing = _REQUIRED_SECTIONS - present - omitted
    if missing:
        raise ValueError(
            f"workspace sections require evidence or omission: {_section_names(missing)}"
        )


def _reject_duplicates(
    references: tuple[EvidenceReference, ...],
    omissions: tuple[EvidenceOmission, ...],
    actions: tuple[NextActionReference, ...],
) -> None:
    reference_keys = [
        (item.section, item.kind, item.authority, item.resource_id)
        for item in references
    ]
    if len(reference_keys) != len(set(reference_keys)):
        raise ValueError("evidence references must be unique")
    omission_sections = [item.section for item in omissions]
    if len(omission_sections) != len(set(omission_sections)):
        raise ValueError("evidence omissions must be unique by section")
    action_keys = [(item.authority, item.action_id) for item in actions]
    if len(action_keys) != len(set(action_keys)):
        raise ValueError("next actions must be unique")


def _reference_to_dict(item: EvidenceReference) -> dict[str, object]:
    return {
        "section": item.section.value,
        "kind": item.kind,
        "authority": item.authority,
        "resource_id": item.resource_id,
        "owner_id": item.owner_id,
        "workspace_id": item.workspace_id,
        "revision": item.revision,
        "sha256": item.sha256,
        "state": item.state,
        "freshness": item.freshness.value,
    }


def _reference_from_dict(payload: object) -> EvidenceReference:
    data = _required_mapping(payload, "reference")
    _require_fields(
        data,
        {
            "section",
            "kind",
            "authority",
            "resource_id",
            "owner_id",
            "workspace_id",
            "revision",
            "sha256",
            "state",
            "freshness",
        },
        "reference",
    )
    return EvidenceReference(
        section=EvidenceSection(str(data["section"])),
        kind=str(data["kind"]),
        authority=str(data["authority"]),
        resource_id=str(data["resource_id"]),
        owner_id=str(data["owner_id"]),
        workspace_id=str(data["workspace_id"]),
        revision=str(data["revision"]),
        sha256=str(data["sha256"]),
        state=str(data["state"]),
        freshness=EvidenceFreshness(str(data["freshness"])),
    )


def _change_set_to_dict(item: ChangeSetProjection) -> dict[str, object]:
    return {
        "authority": item.authority,
        "owner_id": item.owner_id,
        "workspace_id": item.workspace_id,
        "revision": item.revision,
        "base_sha256": item.base_sha256,
        "patch_sha256": item.patch_sha256,
        "changed_files": list(item.changed_files),
        "truncated": item.truncated,
        "freshness": item.freshness.value,
    }


def _change_set_from_dict(data: Mapping[str, Any]) -> ChangeSetProjection:
    _require_fields(
        data,
        {
            "authority",
            "owner_id",
            "workspace_id",
            "revision",
            "base_sha256",
            "patch_sha256",
            "changed_files",
            "truncated",
            "freshness",
        },
        "change_set",
    )
    changed_files = _required_list(data["changed_files"], "changed_files")
    if not isinstance(data["truncated"], bool):
        raise ValueError("change_set truncated must be a boolean")
    return ChangeSetProjection(
        authority=str(data["authority"]),
        owner_id=str(data["owner_id"]),
        workspace_id=str(data["workspace_id"]),
        revision=str(data["revision"]),
        base_sha256=str(data["base_sha256"]),
        patch_sha256=str(data["patch_sha256"]),
        changed_files=tuple(str(item) for item in changed_files),
        truncated=data["truncated"],
        freshness=EvidenceFreshness(str(data["freshness"])),
    )


def _omission_to_dict(item: EvidenceOmission) -> dict[str, str]:
    return {
        "section": item.section.value,
        "reason": item.reason.value,
        "authority": item.authority,
    }


def _omission_from_dict(payload: object) -> EvidenceOmission:
    data = _required_mapping(payload, "omission")
    _require_fields(data, {"section", "reason", "authority"}, "omission")
    return EvidenceOmission(
        section=EvidenceSection(str(data["section"])),
        reason=EvidenceOmissionReason(str(data["reason"])),
        authority=str(data["authority"]),
    )


def _action_to_dict(item: NextActionReference) -> dict[str, object]:
    return {
        "action_id": item.action_id,
        "kind": item.kind,
        "authority": item.authority,
        "owner_id": item.owner_id,
        "workspace_id": item.workspace_id,
        "revision": item.revision,
        "sha256": item.sha256,
        "consequence": item.consequence,
        "expires_at": item.expires_at,
    }


def _action_from_dict(payload: object) -> NextActionReference:
    data = _required_mapping(payload, "next_action")
    _require_fields(
        data,
        {
            "action_id",
            "kind",
            "authority",
            "owner_id",
            "workspace_id",
            "revision",
            "sha256",
            "consequence",
            "expires_at",
        },
        "next_action",
    )
    return NextActionReference(
        action_id=str(data["action_id"]),
        kind=str(data["kind"]),
        authority=str(data["authority"]),
        owner_id=str(data["owner_id"]),
        workspace_id=str(data["workspace_id"]),
        revision=str(data["revision"]),
        sha256=str(data["sha256"]),
        consequence=str(data["consequence"]),
        expires_at=(None if data["expires_at"] is None else str(data["expires_at"])),
    )


def _required_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} fields must be strings")
    return cast(Mapping[str, Any], value)


def _required_list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _require_fields(
    value: Mapping[str, Any],
    fields: set[str],
    name: str,
) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} fields are invalid")


def _required_identity(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTITY_RE.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text


def _required_token(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text


def _required_sha256(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return text


def _required_relative_path(value: object) -> str:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        not text
        or len(text) > 512
        or "\\" in text
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or "\x00" in text
        or path.as_posix() != text
    ):
        raise ValueError("changed file must be a canonical relative POSIX path")
    return text


def _section_names(sections: Iterable[EvidenceSection]) -> str:
    return ", ".join(sorted(item.value for item in sections))


def _json_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
